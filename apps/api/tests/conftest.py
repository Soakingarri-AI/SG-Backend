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

import re  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete, text  # noqa: E402

from app.core.database import AsyncSessionLocal, Base, engine  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import email_service  # noqa: E402

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


@pytest.fixture(autouse=True)
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture outbound mail instead of calling Resend.

    Autouse, so no test can reach the network. Only the transport is replaced —
    the templates really render, so a broken placeholder fails the suite.
    """
    outbox: list[dict] = []

    async def _capture(
        *, to: str, subject: str, html: str, text: str, tag: str = "transactional"
    ) -> bool:
        outbox.append(
            {"to": to, "subject": subject, "html": html, "text": text, "tag": tag}
        )
        return True

    monkeypatch.setattr(email_service, "send_email", _capture)
    return outbox


def _extract_token(message: dict) -> str:
    """Pull the ?token=... value out of a captured email's link."""
    match = re.search(r"[?&]token=([\w\-]+)", message["text"])
    assert match, f"no token link in email: {message['subject']}"
    return match.group(1)


@pytest.fixture
def token_from_email():
    """Expose the link-token extractor to tests."""
    return _extract_token


@pytest.fixture
def new_email():
    """Unique address matching the cleanup filter in ``_clean``."""

    def _make(prefix: str = "authtest") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"

    return _make


@pytest.fixture
def signup(client: AsyncClient, sent_emails: list[dict], new_email):
    """Register (and by default verify) an account, returning its credentials.

    Verification runs through the real endpoint using the token from the real
    rendered email, so callers get an account that can actually log in.
    """

    async def _signup(
        *,
        email: str | None = None,
        password: str = "supersecret1",
        full_name: str | None = "Test User",
        verify: bool = True,
    ) -> dict:
        email = email or new_email()
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": full_name},
        )
        assert resp.status_code == 201, resp.text

        token = _extract_token(sent_emails[-1])
        if verify:
            confirmed = await client.post(
                "/api/v1/auth/verify-email", json={"token": token}
            )
            assert confirmed.status_code == 200, confirmed.text
        return {"email": email, "password": password, "token": token}

    return _signup


@pytest.fixture
def login(client: AsyncClient):
    """Log in and return the JSON body (token pair + user)."""

    async def _login(email: str, password: str = "supersecret1") -> dict:
        resp = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _login


@pytest.fixture
def auth_headers(signup, login):
    """Bearer headers for a freshly created, verified account."""

    async def _headers(prefix: str = "authtest") -> dict[str, str]:
        account = await signup()
        tokens = await login(account["email"], account["password"])
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    return _headers
