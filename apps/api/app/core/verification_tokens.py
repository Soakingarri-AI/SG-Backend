"""Single-use, expiring tokens for email verification and password reset.

These are opaque random strings mailed to a user, not JWTs — they carry no
claims and are only meaningful while their Redis entry lives.

Three properties matter:

* **Hashed at rest.** Redis stores SHA-256 of the token, never the token
  itself, so a dump of the keyspace does not hand over usable links.
* **Single use.** Redemption is a ``GETDEL``, so two concurrent redemptions
  cannot both succeed.
* **One live token per purpose.** Issuing a new token revokes the previous
  one, so an old link in an older email stops working.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Literal

from app.core.redis import get_redis

TokenPurpose = Literal["verify_email", "password_reset"]

_PREFIX = "vtoken"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _token_key(purpose: TokenPurpose, token: str) -> str:
    return f"{_PREFIX}:{purpose}:{_hash(token)}"


def _owner_key(purpose: TokenPurpose, user_id: str) -> str:
    """Points at the user's currently-live token hash for this purpose."""
    return f"{_PREFIX}:{purpose}:owner:{user_id}"


async def issue(purpose: TokenPurpose, user_id: str, ttl_seconds: int) -> str:
    """Mint a token for ``user_id``, invalidating any previous one."""
    redis = get_redis()
    owner_key = _owner_key(purpose, user_id)

    previous = await redis.get(owner_key)
    if previous:
        await redis.delete(f"{_PREFIX}:{purpose}:{previous}")

    token = secrets.token_urlsafe(32)
    digest = _hash(token)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(f"{_PREFIX}:{purpose}:{digest}", user_id, ex=ttl_seconds)
        pipe.set(owner_key, digest, ex=ttl_seconds)
        await pipe.execute()
    return token


async def consume(purpose: TokenPurpose, token: str) -> str | None:
    """Redeem a token, returning its user id — or None if invalid/expired/used."""
    if not token:
        return None
    redis = get_redis()
    # GETDEL makes redemption atomic: a replay finds nothing.
    user_id = await redis.getdel(_token_key(purpose, token))
    if user_id:
        await redis.delete(_owner_key(purpose, user_id))
    return user_id


async def revoke_all(purpose: TokenPurpose, user_id: str) -> None:
    """Drop any live token for this user/purpose (e.g. after a password change)."""
    redis = get_redis()
    owner_key = _owner_key(purpose, user_id)
    digest = await redis.get(owner_key)
    if digest:
        await redis.delete(f"{_PREFIX}:{purpose}:{digest}")
    await redis.delete(owner_key)
