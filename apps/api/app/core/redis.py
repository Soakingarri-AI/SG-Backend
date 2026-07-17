"""Shared async Redis client (ElastiCache in production).

Used for rate limiting, cross-subdomain ephemeral state, and AI response
caching. A single connection pool is created lazily and reused.
"""
from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
