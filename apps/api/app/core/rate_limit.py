"""Redis-backed fixed-window rate limiter exposed as a FastAPI dependency.

Keyed by authenticated user id when present, otherwise by client IP. The window
counter is incremented atomically and given a TTL on first hit, so no separate
sweeper is required.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.core.redis import get_redis


class RateLimiter:
    def __init__(self, per_minute: int | None = None) -> None:
        self.limit = per_minute or settings.RATE_LIMIT_PER_MINUTE

    async def __call__(self, request: Request) -> None:
        redis = get_redis()
        identity = getattr(request.state, "user_id", None) or (
            request.client.host if request.client else "anonymous"
        )
        # 60s fixed window bucket, scoped per-identity and per-endpoint.
        key = f"ratelimit:{identity}:{request.url.path}"

        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, 60)
        if current > self.limit:
            ttl = await redis.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please retry shortly.",
                headers={"Retry-After": str(max(ttl, 1))},
            )


# Default dependency instance.
rate_limit = RateLimiter()


def rate_limited(per_minute: int) -> RateLimiter:
    """Factory for endpoint-specific limits, e.g. `Depends(rate_limited(10))`."""
    return RateLimiter(per_minute=per_minute)
