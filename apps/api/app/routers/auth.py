"""Unified authentication router.

A single account works across every subdomain. On login we set an
``access_token`` cookie scoped to ``.soakingarri.com`` (so the session is shared)
*and* return the token pair in the body for header-based clients.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limited
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import (
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserLogin,
    UserRead,
)

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


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
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


@router.post("/login", response_model=TokenPair, dependencies=[Depends(rate_limited(10))])
async def login(
    payload: UserLogin, response: Response, db: AsyncSession = Depends(get_db)
) -> TokenPair:
    user = await db.scalar(select(User).where(User.email == payload.email))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))
    _set_auth_cookies(response, access, refresh)
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, response: Response) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from exc
    access = create_access_token(claims["sub"])
    new_refresh = create_refresh_token(claims["sub"])
    _set_auth_cookies(response, access, new_refresh)
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    domain = settings.COOKIE_DOMAIN if settings.is_production else None
    response.delete_cookie("access_token", domain=domain)
    response.delete_cookie("refresh_token", domain=domain)


@router.get("/me", response_model=UserRead)
async def me(current: User = Depends(get_current_user)) -> User:
    return current
