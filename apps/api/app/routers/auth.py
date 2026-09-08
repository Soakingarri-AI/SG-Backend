"""Unified authentication router.

A single account works across every subdomain. On login we set an
``access_token`` cookie scoped to ``.soakingarri.com`` (so the session is shared)
*and* return the token pair in the body for header-based clients.

Endpoints stay thin: rules live in ``app.services.auth_service``, token
revocation in ``app.core.token_service``, and mail in
``app.services.email_service``.
"""
from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import token_service
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limited
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    AuthSession,
    EmailVerificationConfirm,
    LogoutRequest,
    MessageResponse,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    ResendVerificationRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    secure = settings.is_production
    response.set_cookie(
        "access_token",
        access,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        domain=settings.COOKIE_DOMAIN if secure else None,
    )
    response.set_cookie(
        "refresh_token",
        refresh,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        secure=secure,
        samesite="lax",
        domain=settings.COOKIE_DOMAIN if secure else None,
    )


def _start_session(response: Response, user: User) -> AuthSession:
    """Issue a token pair for ``user``, set the cookies, and return the body."""
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    _set_auth_cookies(response, access, refresh)
    return AuthSession(
        access_token=access,
        refresh_token=refresh,
        user=UserRead.model_validate(user),
    )


# --------------------------------------------------------------------------- #
# Registration and email verification
# --------------------------------------------------------------------------- #
@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limited(10))],
    summary="Create a new account",
)
async def register(
    payload: UserCreate,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Register a user and email them a verification link.

    Emails are unique across the whole platform (409 on reuse). The account
    exists immediately but cannot sign in until the link is used.
    """
    return await auth_service.register(
        db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        background=background,
    )


@router.post(
    "/verify-email",
    response_model=AuthSession,
    summary="Confirm an email address",
)
async def verify_email(
    payload: EmailVerificationConfirm,
    response: Response,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> AuthSession:
    """Redeem a verification link and sign the user straight in.

    Returns a token pair so the frontend can land the user in the app instead
    of bouncing them back to a login form. `400` if the link is invalid, already
    used, or expired — request a fresh one from `/verify-email/resend`.
    """
    user = await auth_service.verify_email(
        db, token=payload.token, background=background
    )
    return _start_session(response, user)


@router.post(
    "/verify-email/resend",
    response_model=MessageResponse,
    dependencies=[Depends(rate_limited(3))],
    summary="Resend the verification email",
)
async def resend_verification(
    payload: ResendVerificationRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Issue a fresh verification link, invalidating the previous one.

    Always reports the same result so the endpoint cannot be used to discover
    which addresses are registered.
    """
    return await auth_service.resend_verification(
        db, email=payload.email, background=background
    )


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #
@router.post(
    "/login",
    response_model=AuthSession,
    dependencies=[Depends(rate_limited(10))],
    summary="Log in and receive a token pair",
)
async def login(
    payload: UserLogin, response: Response, db: AsyncSession = Depends(get_db)
) -> AuthSession:
    """Verify credentials, set the shared-domain auth cookies, and return tokens.

    `401` for bad credentials. `403` when the address is not yet confirmed —
    that response carries `X-Auth-Error-Code: email_not_verified`, so a client
    can route to a "check your inbox" screen without parsing the message.
    """
    user = await auth_service.authenticate(
        db, email=payload.email, password=payload.password
    )
    return _start_session(response, user)


@router.post("/refresh", response_model=TokenPair, summary="Rotate the token pair")
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = Body(default=None),
) -> TokenPair:
    """Exchange a valid refresh token for a new pair. The token is read from the
    JSON body or, for browser clients, the httponly ``refresh_token`` cookie
    (which client JS cannot read, so no body is required). The old refresh token
    is single-use; reuse — including a concurrent replay — is rejected."""
    raw = (payload.refresh_token if payload else None) or request.cookies.get(
        "refresh_token"
    )
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token missing")
    try:
        claims = decode_token(raw, expected_type="refresh")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from exc

    # Reject a session invalidated by a password reset.
    if await token_service.is_before_epoch(claims["sub"], int(claims["iat"])):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is no longer valid")

    # Rotate atomically: exactly one caller can consume a given refresh token.
    # Losing the claim means the token was already rotated out or revoked.
    if not await token_service.consume_token(claims):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is no longer valid")

    access = create_access_token(claims["sub"])
    new_refresh = create_refresh_token(claims["sub"])
    _set_auth_cookies(response, access, new_refresh)
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    # Explicit: with ``from __future__ import annotations`` the ``-> None``
    # return annotation resolves to NoneType and FastAPI would otherwise treat
    # it as a response model, which a 204 must not have.
    response_model=None,
    summary="Log out and revoke tokens",
)
async def logout(
    request: Request,
    response: Response,
    body: LogoutRequest | None = Body(default=None),
) -> None:
    """Clear the auth cookies and denylist the presented access/refresh tokens so
    they cannot be replayed before expiry."""
    access = request.cookies.get("access_token")
    if not access:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            access = header[7:]
    refresh_token = request.cookies.get("refresh_token") or (
        body.refresh_token if body else None
    )

    for raw, ttype in ((access, "access"), (refresh_token, "refresh")):
        if not raw:
            continue
        try:
            claims = decode_token(raw, expected_type=ttype)  # type: ignore[arg-type]
        except JWTError:
            continue
        await token_service.revoke_token(claims)

    domain = settings.COOKIE_DOMAIN if settings.is_production else None
    response.delete_cookie("access_token", domain=domain)
    response.delete_cookie("refresh_token", domain=domain)


@router.get("/me", response_model=UserRead, summary="Get the current user")
async def me(current: User = Depends(get_current_user)) -> User:
    """Return the authenticated user's profile."""
    return current


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
@router.post(
    "/password-reset/request",
    dependencies=[Depends(rate_limited(5))],
    summary="Request a password reset link",
)
async def request_password_reset(
    payload: PasswordResetRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Email a single-use, time-limited reset link.

    Always returns the same message regardless of whether the email exists, so
    the endpoint cannot be used to enumerate accounts. Only in *development* is
    the token echoed in the body to enable testing (staging mirrors production
    so a shared environment can never leak it).
    """
    return await auth_service.request_password_reset(
        db, email=payload.email, background=background
    )


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    summary="Set a new password with a reset token",
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Consume a reset link, set the new password, and sign every device out.

    Completing this also confirms the address, since receiving the link proves
    control of the mailbox.
    """
    await auth_service.confirm_password_reset(
        db,
        token=payload.token,
        new_password=payload.new_password,
        background=background,
    )
    return {"message": "Password updated. Please log in again."}


@router.post(
    "/password/change",
    response_model=MessageResponse,
    summary="Change your password while signed in",
)
async def change_password(
    payload: PasswordChangeRequest,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict:
    """Re-check the current password, rotate it, and revoke all sessions.

    The caller's own tokens are invalidated too, so clients must log in again
    with the new password.
    """
    await auth_service.change_password(
        db,
        user=current,
        current_password=payload.current_password,
        new_password=payload.new_password,
        background=background,
    )
    return {"message": "Password changed. Please log in again."}
