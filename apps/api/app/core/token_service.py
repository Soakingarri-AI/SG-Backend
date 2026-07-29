"""Redis-backed token revocation and session invalidation.

Two complementary mechanisms make the otherwise-stateless JWTs revocable:

* **Per-token denylist** keyed by the JWT ``jti``. Used for single-device logout
  and refresh-token rotation (the old refresh token is denied the moment a new
  pair is issued, so a leaked/replayed refresh token is rejected).
* **Per-user session epoch**. On a sensitive event (password reset) we record a
  timestamp; any token whose ``iat`` predates that epoch is rejected. This kills
  *every* outstanding session for the user in one write.

Every key carries a TTL equal to the token's remaining lifetime, so Redis
self-cleans and the denylist never grows unbounded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.redis import get_redis

_DENY_PREFIX = "revoked_jti:"
_EPOCH_PREFIX = "session_epoch:"


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def token_ttl_seconds(payload: dict[str, Any]) -> int:
    """Seconds until the token's ``exp``; floored at 1 so keys always expire."""
    exp = int(payload.get("exp", 0))
    return max(exp - _now_ts(), 1)


async def revoke_token(payload: dict[str, Any]) -> None:
    """Denylist a decoded token by its ``jti`` for its remaining lifetime."""
    jti = payload.get("jti")
    if not jti:
        return
    await get_redis().set(f"{_DENY_PREFIX}{jti}", "1", ex=token_ttl_seconds(payload))


async def consume_token(payload: dict[str, Any]) -> bool:
    """Atomically claim single-use of a token (``SET NX`` on its ``jti``).

    Returns True for exactly one caller; False when the token was already
    revoked or consumed — including a concurrent request racing on the same
    token, which is how refresh-token reuse is detected without a
    check-then-set window.
    """
    jti = payload.get("jti")
    if not jti:
        return False
    return bool(
        await get_redis().set(
            f"{_DENY_PREFIX}{jti}", "1", ex=token_ttl_seconds(payload), nx=True
        )
    )


async def is_token_revoked(payload: dict[str, Any]) -> bool:
    jti = payload.get("jti")
    if not jti:
        return False
    return bool(await get_redis().exists(f"{_DENY_PREFIX}{jti}"))


async def invalidate_user_sessions(user_id: str, ttl: int) -> None:
    """Record a session epoch of *now*; all tokens issued earlier are rejected."""
    await get_redis().set(f"{_EPOCH_PREFIX}{user_id}", _now_ts(), ex=ttl)


async def is_before_epoch(user_id: str, issued_at: int) -> bool:
    """True if a token issued at ``issued_at`` predates the user's session epoch."""
    raw = await get_redis().get(f"{_EPOCH_PREFIX}{user_id}")
    if raw is None:
        return False
    return issued_at < int(raw)
