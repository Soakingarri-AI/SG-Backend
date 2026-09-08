"""Auth request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# NIST-style rules: enforce length, screen the obvious, skip composition rules
# (forced symbols push people toward "Passw0rd!" and worse reuse).
Password = Annotated[str, Field(min_length=8, max_length=128)]

_COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "12345678", "123456789",
        "1234567890", "qwertyuiop", "qwerty123", "iloveyou", "admin123",
        "welcome1", "letmein1", "abc12345", "football", "sunshine",
        "princess", "changeme", "passw0rd", "soakingarri",
    }
)


def _screen_password(value: str) -> str:
    if value.lower() in _COMMON_PASSWORDS:
        raise ValueError("This password is too common. Please choose another.")
    if len(set(value)) < 4:
        raise ValueError("Please choose a password with more variety.")
    return value


class UserCreate(BaseModel):
    email: EmailStr
    password: Password
    full_name: str | None = Field(default=None, max_length=120)

    _check_password = field_validator("password")(_screen_password)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    is_verified: bool
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthSession(TokenPair):
    """A token pair plus the profile it belongs to, for post-auth screens."""

    user: UserRead


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """Optional body so header-based clients can revoke their refresh token too."""

    refresh_token: str | None = None


class MessageResponse(BaseModel):
    message: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1)
    new_password: Password

    _check_password = field_validator("new_password")(_screen_password)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: Password

    _check_password = field_validator("new_password")(_screen_password)


class EmailVerificationConfirm(BaseModel):
    token: str = Field(min_length=1)


class ResendVerificationRequest(BaseModel):
    email: EmailStr
