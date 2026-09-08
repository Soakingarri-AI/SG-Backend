"""Auth flow tests: registration, login, token revocation, and password reset.

Proves the security guarantees added on top of the base JWT flow:
  * logout denylists the presented access token,
  * refresh rotation makes a used refresh token single-use (reuse detection),
  * a password reset kills every outstanding session.

Email verification has its own module (``test_auth_email.py``); the ``signup``
fixture here registers *and* confirms an account so these tests exercise
sessions rather than the verification gate.
"""
from __future__ import annotations

import asyncio

from httpx import AsyncClient

AUTH = "/api/v1/auth"


# --------------------------------------------------------------------------- #
# Registration + login
# --------------------------------------------------------------------------- #
async def test_register_and_me(client: AsyncClient, signup, login) -> None:
    account = await signup()
    tokens = await login(account["email"], account["password"])

    resp = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == account["email"]


async def test_login_returns_the_profile_with_the_tokens(signup, login) -> None:
    """Clients need `is_verified` at login without a second round trip."""
    account = await signup()
    body = await login(account["email"], account["password"])

    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == account["email"]
    assert body["user"]["is_verified"] is True


async def test_register_duplicate_email_conflicts(client: AsyncClient, signup) -> None:
    account = await signup()
    resp = await client.post(
        f"{AUTH}/register",
        json={"email": account["email"], "password": "supersecret1"},
    )
    assert resp.status_code == 409


async def test_login_with_wrong_password_rejected(
    client: AsyncClient, signup
) -> None:
    account = await signup()
    resp = await client.post(
        f"{AUTH}/login", json={"email": account["email"], "password": "wrongpassword"}
    )
    assert resp.status_code == 401


async def test_unknown_email_and_wrong_password_are_indistinguishable(
    client: AsyncClient, signup, new_email
) -> None:
    """Both must answer 401 with the same detail, or login enumerates accounts."""
    account = await signup()
    wrong_password = await client.post(
        f"{AUTH}/login", json={"email": account["email"], "password": "wrongpassword"}
    )
    unknown_user = await client.post(
        f"{AUTH}/login", json={"email": new_email(), "password": "wrongpassword"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


async def test_me_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get(f"{AUTH}/me")
    assert resp.status_code == 401


async def test_weak_passwords_rejected(client: AsyncClient, new_email) -> None:
    for weak in ("short", "password123", "aaaaaaaaaa"):
        resp = await client.post(
            f"{AUTH}/register", json={"email": new_email(), "password": weak}
        )
        assert resp.status_code == 422, weak


# --------------------------------------------------------------------------- #
# Token revocation
# --------------------------------------------------------------------------- #
async def test_logout_revokes_access_token(
    client: AsyncClient, signup, login
) -> None:
    account = await signup()
    tokens = await login(account["email"], account["password"])
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    # Token works before logout.
    assert (await client.get(f"{AUTH}/me", headers=auth)).status_code == 200

    resp = await client.post(
        f"{AUTH}/logout", headers=auth, json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 204

    # Same token is now denylisted.
    assert (await client.get(f"{AUTH}/me", headers=auth)).status_code == 401


async def test_refresh_rotation_invalidates_old_refresh_token(
    client: AsyncClient, signup, login
) -> None:
    account = await signup()
    tokens = await login(account["email"], account["password"])
    old_refresh = tokens["refresh_token"]

    # First refresh succeeds and returns a new pair.
    first = await client.post(f"{AUTH}/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200
    new_access = first.json()["access_token"]

    # Replaying the *old* refresh token is rejected (single-use rotation).
    replay = await client.post(f"{AUTH}/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401

    # The freshly minted access token still works.
    resp = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert resp.status_code == 200


async def test_refresh_falls_back_to_cookie(
    client: AsyncClient, signup, login
) -> None:
    """Browser clients can't read the httponly refresh cookie, so the endpoint
    must accept the cookie directly when no body is sent."""
    account = await signup()
    await login(account["email"], account["password"])  # sets cookies on the jar

    resp = await client.post(f"{AUTH}/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]

    # The rotated pair works.
    me = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_without_token_rejected(client: AsyncClient) -> None:
    resp = await client.post(f"{AUTH}/refresh")
    assert resp.status_code == 401


async def test_concurrent_refresh_has_exactly_one_winner(
    client: AsyncClient, signup, login
) -> None:
    """Rotation is atomic (SET NX): racing requests with the same refresh token
    must produce exactly one new pair, never several."""
    account = await signup()
    tokens = await login(account["email"], account["password"])

    responses = await asyncio.gather(
        *[
            client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
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
    client: AsyncClient, new_email, sent_emails
) -> None:
    resp = await client.post(
        f"{AUTH}/password-reset/request", json={"email": new_email()}
    )
    assert resp.status_code == 200
    # No token is issued and no mail is sent for a non-existent account.
    assert "reset_token" not in resp.json()
    assert sent_emails == []


async def test_password_reset_flow_kills_sessions_and_changes_password(
    client: AsyncClient, signup, login
) -> None:
    account = await signup(password="oldpassword1")
    tokens = await login(account["email"], "oldpassword1")
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.get(f"{AUTH}/me", headers=auth)).status_code == 200

    # Request a reset token (returned in body outside production).
    req = await client.post(
        f"{AUTH}/password-reset/request", json={"email": account["email"]}
    )
    assert req.status_code == 200
    reset_token = req.json()["reset_token"]

    # Cross the 1-second iat boundary so the pre-reset token predates the epoch.
    await asyncio.sleep(1.1)

    confirm = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": reset_token, "new_password": "newpassword1"},
    )
    assert confirm.status_code == 200

    # Old session is dead.
    assert (await client.get(f"{AUTH}/me", headers=auth)).status_code == 401

    # Old password no longer works; the new one does.
    assert (
        await client.post(
            f"{AUTH}/login",
            json={"email": account["email"], "password": "oldpassword1"},
        )
    ).status_code == 401
    await login(account["email"], "newpassword1")

    # A reset token is single-use.
    reuse = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": reset_token, "new_password": "another1pw"},
    )
    assert reuse.status_code == 400


async def test_requesting_a_second_reset_link_invalidates_the_first(
    client: AsyncClient, signup
) -> None:
    """Only the newest link may work, or an old mail stays a live credential."""
    account = await signup()

    first = (
        await client.post(
            f"{AUTH}/password-reset/request", json={"email": account["email"]}
        )
    ).json()["reset_token"]
    second = (
        await client.post(
            f"{AUTH}/password-reset/request", json={"email": account["email"]}
        )
    ).json()["reset_token"]
    assert first != second

    stale = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": first, "new_password": "newpassword1"},
    )
    assert stale.status_code == 400

    current = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": second, "new_password": "newpassword1"},
    )
    assert current.status_code == 200
