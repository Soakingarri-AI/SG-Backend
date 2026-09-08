"""Authentication business logic.

The router owns HTTP concerns (cookies, status codes, dependencies); everything
below owns the rules — who may log in, how ownership of an address is proven,
and what happens to live sessions when a password changes.

Two conventions worth knowing:

* Endpoints that take an email address never reveal whether it is registered.
  Reset and resend always report the same thing, so they cannot be used to
  enumerate accounts.
* Email dispatch is handed to a background task. A mail outage must never stop
  someone registering or resetting their password.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import token_service, verification_tokens
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.services import email_service

logger = logging.getLogger(__name__)

# Verifying this pre-computed hash costs the same as verifying a real one, so a
# login for an unknown address takes as long as one for a known address and the
# response time stops leaking which accounts exist.
_DUMMY_HASH = hash_password("timing-equalisation-placeholder")

UNVERIFIED_CODE = "email_not_verified"
_GENERIC_EMAIL_RESPONSE = {
    "message": "If that email is registered, we've sent a link to it."
}


def _verification_ttl() -> int:
    return settings.EMAIL_VERIFICATION_EXPIRE_HOURS * 3600


def _reset_ttl() -> int:
    return settings.PASSWORD_RESET_EXPIRE_MINUTES * 60


async def _get_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


# --------------------------------------------------------------------------- #
# Registration and verification
# --------------------------------------------------------------------------- #
async def register(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None,
    background: BackgroundTasks,
) -> User:
    """Create an account and send its verification link."""
    if await _get_by_email(db, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        is_verified=False,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    token = await verification_tokens.issue(
        "verify_email", str(user.id), _verification_ttl()
    )
    background.add_task(
        email_service.send_verification_email,
        email=user.email,
        full_name=user.full_name,
        token=token,
    )
    return user


async def verify_email(
    db: AsyncSession, *, token: str, background: BackgroundTasks
) -> User:
    """Redeem a verification token and activate the account."""
    user_id = await verification_tokens.consume("verify_email", token)
    if not user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid or expired verification link"
        )

    user = await db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid or expired verification link"
        )

    if not user.is_verified:
        user.is_verified = True
        user.verified_at = datetime.now(UTC)
        background.add_task(
            email_service.send_welcome_email,
            email=user.email,
            full_name=user.full_name,
        )
    return user


async def resend_verification(
    db: AsyncSession, *, email: str, background: BackgroundTasks
) -> dict:
    """Re-issue a verification link, without disclosing whether the account exists."""
    user = await _get_by_email(db, email)
    if user is not None and not user.is_verified:
        token = await verification_tokens.issue(
            "verify_email", str(user.id), _verification_ttl()
        )
        background.add_task(
            email_service.send_verification_email,
            email=user.email,
            full_name=user.full_name,
            token=token,
        )
    return _GENERIC_EMAIL_RESPONSE


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    """Return the user for valid credentials, else raise 401/403."""
    user = await _get_by_email(db, email)

    if user is None:
        verify_password(password, _DUMMY_HASH)  # equalise timing, then fail
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is disabled")

    if settings.REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
        # Human-readable detail for naive UIs; the header lets a client branch
        # to a "resend verification" screen without parsing prose.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Please confirm your email address before signing in. "
            "Check your inbox for the verification link.",
            headers={"X-Auth-Error-Code": UNVERIFIED_CODE},
        )
    return user


# --------------------------------------------------------------------------- #
# Password reset and change
# --------------------------------------------------------------------------- #
async def request_password_reset(
    db: AsyncSession, *, email: str, background: BackgroundTasks
) -> dict:
    """Send a reset link if the address is registered; response never varies."""
    user = await _get_by_email(db, email)
    reset_token: str | None = None

    if user is not None:
        reset_token = await verification_tokens.issue(
            "password_reset", str(user.id), _reset_ttl()
        )
        background.add_task(
            email_service.send_password_reset_email,
            email=user.email,
            full_name=user.full_name,
            token=reset_token,
        )

    body = dict(_GENERIC_EMAIL_RESPONSE)
    # Development only: lets local tests complete the flow without a mailbox.
    # Staging mirrors production precisely so a shared env can never leak it.
    if reset_token and settings.ENVIRONMENT == "development":
        body["reset_token"] = reset_token
    return body


async def confirm_password_reset(
    db: AsyncSession, *, token: str, new_password: str, background: BackgroundTasks
) -> User:
    """Set a new password from a reset link and sign every device out."""
    user_id = await verification_tokens.consume("password_reset", token)
    if not user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token"
        )

    user = await db.get(User, uuid.UUID(user_id))
    if user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token"
        )

    await _apply_new_password(db, user, new_password, background)

    # Receiving the link proves control of the address, so an unverified
    # account that resets its password is verified by the same act.
    if not user.is_verified:
        user.is_verified = True
        user.verified_at = datetime.now(UTC)
    return user


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    background: BackgroundTasks,
) -> None:
    """Change the password of a signed-in user after re-checking the old one."""
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Current password is incorrect"
        )
    if current_password == new_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "New password must be different from the current one",
        )
    await _apply_new_password(db, user, new_password, background)


async def _apply_new_password(
    db: AsyncSession, user: User, new_password: str, background: BackgroundTasks
) -> None:
    """Shared tail of every password change: rotate, revoke, notify."""
    user.hashed_password = hash_password(new_password)

    # Kill every outstanding session so a compromised password cannot linger,
    # and drop any unused reset link that was issued before this change.
    await token_service.invalidate_user_sessions(
        str(user.id), settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    )
    await verification_tokens.revoke_all("password_reset", str(user.id))

    background.add_task(
        email_service.send_password_changed_email,
        email=user.email,
        full_name=user.full_name,
    )
