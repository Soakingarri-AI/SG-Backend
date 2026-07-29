"""Unified authentication router.

A single account works across every subdomain. On login we set an
``access_token`` cookie scoped to ``.soakingarri.com`` (so the session is shared)
*and* return the token pair in the body for header-based clients.
"""
from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limited
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core import token_service
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    LogoutRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_RESET_PREFIX = "pwdreset:"


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


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    """Register a user. Emails are unique across the whole platform (409 on reuse)."""
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(rate_limited(10))],
    summary="Log in and receive a token pair",
)
async def login(
    payload: UserLogin, response: Response, db: AsyncSession = Depends(get_db)
) -> TokenPair:
    """Verify credentials, set the shared-domain auth cookies, and return the tokens."""
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    _set_auth_cookies(response, access, refresh)
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post(
    "/refresh", response_model=TokenPair, summary="Rotate the token pair"
)
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
    # Revoke whatever tokens the client presented (cookies and/or headers) so
    # they cannot be replayed before their natural expiry.
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


@router.post(
    "/password-reset/request",
    dependencies=[Depends(rate_limited(5))],
    summary="Request a password reset token",
)
async def request_password_reset(
    payload: PasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Issue a single-use, time-limited reset token.

    Always returns the same message regardless of whether the email exists, so
    the endpoint cannot be used to enumerate accounts. The token is delivered by
    email; only in *development* is it returned in the body to enable testing
    (staging mirrors production so a shared environment can never leak it).
    """
    user = await db.scalar(select(User).where(User.email == payload.email))
    reset_token: str | None = None
    if user is not None:
        reset_token = secrets.token_urlsafe(32)
        await get_redis().set(
            f"{_RESET_PREFIX}{reset_token}",
            str(user.id),
            ex=settings.PASSWORD_RESET_EXPIRE_MINUTES * 60,
        )
        # TODO: dispatch the reset email (SES) with the token link.

    body: dict = {"message": "If that email is registered, a reset link has been sent."}
    if reset_token and settings.ENVIRONMENT == "development":
        body["reset_token"] = reset_token
    return body


@router.post(
    "/password-reset/confirm", summary="Set a new password with a reset token"
)
async def confirm_password_reset(
    payload: PasswordResetConfirm, db: AsyncSession = Depends(get_db)
) -> dict:
    redis = get_redis()
    key = f"{_RESET_PREFIX}{payload.token}"
    user_id = await redis.get(key)
    if not user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token"
        )
    await redis.delete(key)  # single use

    user = await db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    user.hashed_password = hash_password(payload.new_password)
    # Kill every outstanding session so a compromised password can't linger.
    await token_service.invalidate_user_sessions(
        str(user.id), settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    )
    return {"message": "Password updated. Please log in again."}
