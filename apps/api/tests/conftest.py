"""Shared pytest fixtures for the API test suite.

The tests run against the docker-compose Postgres (pgvector) + Redis services.
To stay isolated from dev data we:
  * point Redis at DB index 1 (``/1``) and flush it between tests, and
  * confine all writes to throwaway ``@test.local`` users, cleaned up per test.

Environment is configured *before* any app module is imported, because settings
are cached at import time.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://soakingarri:soakingarri_dev@localhost:5432/soakingarri",
)
# Isolate ephemeral state (rate-limit counters, denylist) in a dedicated Redis DB
# index. A container run can override this via -e REDIS_URL=redis://redis:6379/1.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete, text  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402

# ``.local`` is a rejected special-use domain, so tests use example.com with a
# distinctive local-part prefix that the cleanup below can target safely.
_TEST_EMAIL_LIKE = "authtest-%@example.com"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> None:
    """Ensure the schema (and pgvector extension) exists for the test run."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean() -> None:
    """Reset ephemeral Redis state and remove test users before and after each test."""

    async def _wipe() -> None:
        await get_redis().flushdb()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.email.like(_TEST_EMAIL_LIKE)))
            await db.commit()

    await _wipe()
    yield
    await _wipe()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
