"""Redis-backed fixed-window rate limiter exposed as a FastAPI dependency.

Keyed by authenticated user id when the request carries a valid access token,
otherwise by client IP. The token is decoded locally here (signature check
only, no DB or revocation lookup) because router-level dependencies run
*before* ``get_current_user``, so ``request.state`` is not yet populated.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status
from jose import JWTError

from app.core.config import settings
from app.core.redis import get_redis
from app.core.security import decode_token


def _client_ip(request: Request) -> str:
    """Best-effort client IP, proxy-aware.

    Behind the ALB ``request.client.host`` is the load balancer's address, so
    every user would share one bucket. Use the rightmost ``X-Forwarded-For``
    entry — the one appended by the trusted proxy in front of us; leftmost
    entries are client-supplied and trivially spoofable.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "anonymous"


def _identity(request: Request) -> str:
    """Rate-limit key: ``user:<id>`` when authenticated, ``ip:<addr>`` otherwise."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token: str | None = header[7:]
    else:
        token = request.cookies.get("access_token")
    if token:
        try:
            return f"user:{decode_token(token, expected_type='access')['sub']}"
        except JWTError:
            pass
    return f"ip:{_client_ip(request)}"


class RateLimiter:
    def __init__(self, per_minute: int | None = None) -> None:
        self.limit = per_minute or settings.RATE_LIMIT_PER_MINUTE

    async def __call__(self, request: Request) -> None:
        redis = get_redis()
        # 60s fixed window bucket, scoped per-identity and per-endpoint.
        key = f"ratelimit:{_identity(request)}:{request.url.path}"

        # INCR + EXPIRE in one pipeline; ``nx`` only sets the TTL on the first
        # hit, so a crash between the commands can't leave an immortal counter.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, 60, nx=True)
            current, _ = await pipe.execute()
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
