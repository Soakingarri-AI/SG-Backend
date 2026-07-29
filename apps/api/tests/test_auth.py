"""Auth flow tests: registration, login, token revocation, and password reset.

Proves the security guarantees added on top of the base JWT flow:
  * logout denylists the presented access token,
  * refresh rotation makes a used refresh token single-use (reuse detection),
  * a password reset kills every outstanding session.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _email() -> str:
    return f"authtest-{uuid.uuid4().hex[:12]}@example.com"


async def _register(client: AsyncClient, email: str, password: str = "supersecret1") -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Test User"},
    )
    assert resp.status_code == 201, resp.text


async def _login(client: AsyncClient, email: str, password: str = "supersecret1") -> dict:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Registration + login
# --------------------------------------------------------------------------- #
async def test_register_and_me(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    tokens = await _login(client, email)

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == email


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret1"},
    )
    assert resp.status_code == 409


async def test_login_with_wrong_password_rejected(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "wrongpassword"}
    )
    assert resp.status_code == 401


async def test_me_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Token revocation
# --------------------------------------------------------------------------- #
async def test_logout_revokes_access_token(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    tokens = await _login(client, email)
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Token works before logout.
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 200

    resp = await client.post(
        "/api/v1/auth/logout",
        headers=auth,
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 204

    # Same token is now denylisted.
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401


async def test_refresh_rotation_invalidates_old_refresh_token(client: AsyncClient) -> None:
    email = _email()
    await _register(client, email)
    tokens = await _login(client, email)
    old_refresh = tokens["refresh_token"]

    # First refresh succeeds and returns a new pair.
    first = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert first.status_code == 200
    new_access = first.json()["access_token"]

    # Replaying the *old* refresh token is rejected (single-use rotation).
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert replay.status_code == 401

    # The freshly minted access token still works.
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert resp.status_code == 200


async def test_refresh_falls_back_to_cookie(client: AsyncClient) -> None:
    """Browser clients can't read the httponly refresh cookie, so the endpoint
    must accept the cookie directly when no body is sent."""
    email = _email()
    await _register(client, email)
    await _login(client, email)  # login sets the cookies on the client jar

    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]

    # The rotated pair works.
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_without_token_rejected(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


async def test_concurrent_refresh_has_exactly_one_winner(client: AsyncClient) -> None:
    """Rotation is atomic (SET NX): racing requests with the same refresh token
    must produce exactly one new pair, never several."""
    email = _email()
    await _register(client, email)
    tokens = await _login(client, email)

    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            for _ in range(5)
        ]
    )
    statuses = [r.status_code for r in responses]
    assert statuses.count(200) == 1
    assert statuses.count(401) == 4


# --------------------------------------------------------------------------- #
# Password reset
# --------------------------------------------------------------------------- #
async def test_password_reset_request_does_not_leak_unknown_email(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": _email()},
    )
    assert resp.status_code == 200
    # No token is issued for a non-existent account.
    assert "reset_token" not in resp.json()


async def test_password_reset_flow_kills_sessions_and_changes_password(
    client: AsyncClient,
) -> None:
    email = _email()
    await _register(client, email, password="oldpassword1")
    tokens = await _login(client, email, password="oldpassword1")
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 200

    # Request a reset token (returned in body outside production).
    req = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": email}
    )
    assert req.status_code == 200
    reset_token = req.json()["reset_token"]

    # Cross the 1-second iat boundary so the pre-reset token predates the epoch.
    await asyncio.sleep(1.1)

    confirm = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "newpassword1"},
    )
    assert confirm.status_code == 200

    # Old session is dead.
    assert (await client.get("/api/v1/auth/me", headers=auth)).status_code == 401

    # Old password no longer works; the new one does.
    assert (
        await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "oldpassword1"}
        )
    ).status_code == 401
    await _login(client, email, password="newpassword1")

    # A reset token is single-use.
    reuse = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "another1pw"},
    )
    assert reuse.status_code == 400
