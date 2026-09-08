"""Email verification, resend, and password-change flows.

The ``sent_emails`` fixture replaces only the network call, so every assertion
here runs against a really-rendered template.
"""
from __future__ import annotations

import asyncio
import re

from httpx import AsyncClient

from app.core.config import settings

AUTH = "/api/v1/auth"


# --------------------------------------------------------------------------- #
# Registration sends a usable verification email
# --------------------------------------------------------------------------- #
async def test_register_sends_verification_email(
    client: AsyncClient, new_email, sent_emails
) -> None:
    email = new_email()
    resp = await client.post(
        f"{AUTH}/register",
        json={"email": email, "password": "supersecret1", "full_name": "Amina Ismaila"},
    )
    assert resp.status_code == 201
    assert resp.json()["is_verified"] is False

    assert len(sent_emails) == 1
    message = sent_emails[0]
    assert message["to"] == email
    assert message["tag"] == "verify_email"
    assert "Confirm your email" in message["subject"]
    # Personalised with the first name only.
    assert "Hi Amina" in message["text"]
    assert "Hi Amina" in message["html"]


async def test_verification_email_links_to_the_frontend(
    client: AsyncClient, signup, sent_emails
) -> None:
    await signup(verify=False)
    message = sent_emails[-1]
    link = re.search(r"https?://\S+/verify-email\?token=[\w\-]+", message["text"])
    assert link, message["text"]
    assert link.group(0).startswith(settings.FRONTEND_URL.rstrip("/"))
    # The same link is in the HTML button.
    assert link.group(0) in message["html"]


async def test_rendered_email_is_complete_html(
    client: AsyncClient, signup, sent_emails
) -> None:
    """Guards against a template placeholder that never got substituted."""
    await signup(verify=False)
    html = sent_emails[-1]["html"]

    assert html.lstrip().startswith("<!DOCTYPE")
    assert settings.EMAIL_LOGO_URL in html  # logo renders
    assert "#e0a034" in html  # brand gold
    assert not re.search(r"\$\w+", html), "unsubstituted placeholder in template"
    assert "</html>" in html


async def test_email_copy_reads_as_human_writing(
    client: AsyncClient, signup, sent_emails
) -> None:
    """No em dashes anywhere in what a recipient reads."""
    account = await signup(verify=False)  # verification email
    await client.post(f"{AUTH}/verify-email", json={"token": account["token"]})  # welcome
    await client.post(
        f"{AUTH}/password-reset/request", json={"email": account["email"]}
    )  # reset

    assert len(sent_emails) >= 3
    for message in sent_emails:
        for part in ("subject", "html", "text"):
            assert "—" not in message[part], (
                f"em dash in {part} of {message['subject']!r}"
            )


# --------------------------------------------------------------------------- #
# The verification gate
# --------------------------------------------------------------------------- #
async def test_unverified_account_cannot_log_in(
    client: AsyncClient, signup
) -> None:
    account = await signup(verify=False)
    resp = await client.post(
        f"{AUTH}/login",
        json={"email": account["email"], "password": account["password"]},
    )
    assert resp.status_code == 403
    # Machine-readable branch for the "check your inbox" screen.
    assert resp.headers.get("X-Auth-Error-Code") == "email_not_verified"
    # Human-readable detail stays a plain string for naive UIs.
    assert isinstance(resp.json()["detail"], str)


async def test_verification_signs_the_user_in_and_welcomes_them(
    client: AsyncClient, signup, sent_emails
) -> None:
    account = await signup(verify=False)
    resp = await client.post(f"{AUTH}/verify-email", json={"token": account["token"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["is_verified"] is True
    # Landed straight in the app: the returned token works.
    me = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["is_verified"] is True

    assert sent_emails[-1]["tag"] == "welcome"
    assert "Welcome" in sent_emails[-1]["subject"]

    # And login now works normally.
    login = await client.post(
        f"{AUTH}/login",
        json={"email": account["email"], "password": account["password"]},
    )
    assert login.status_code == 200


async def test_verification_token_is_single_use(
    client: AsyncClient, signup
) -> None:
    account = await signup(verify=False)
    first = await client.post(f"{AUTH}/verify-email", json={"token": account["token"]})
    assert first.status_code == 200

    replay = await client.post(f"{AUTH}/verify-email", json={"token": account["token"]})
    assert replay.status_code == 400


async def test_invalid_verification_token_rejected(client: AsyncClient) -> None:
    resp = await client.post(f"{AUTH}/verify-email", json={"token": "not-a-real-token"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Resend
# --------------------------------------------------------------------------- #
async def test_resend_issues_a_new_token_and_kills_the_old_one(
    client: AsyncClient, signup, sent_emails, token_from_email
) -> None:
    account = await signup(verify=False)

    resp = await client.post(
        f"{AUTH}/verify-email/resend", json={"email": account["email"]}
    )
    assert resp.status_code == 200

    fresh = token_from_email(sent_emails[-1])
    assert fresh != account["token"]

    # The superseded link no longer works...
    stale = await client.post(f"{AUTH}/verify-email", json={"token": account["token"]})
    assert stale.status_code == 400
    # ...but the new one does.
    good = await client.post(f"{AUTH}/verify-email", json={"token": fresh})
    assert good.status_code == 200


async def test_resend_does_not_leak_account_existence(
    client: AsyncClient, signup, new_email, sent_emails
) -> None:
    unknown = await client.post(
        f"{AUTH}/verify-email/resend", json={"email": new_email()}
    )
    assert unknown.status_code == 200
    assert sent_emails == []  # nothing sent for an unknown address

    verified = await signup()
    sent_emails.clear()
    already = await client.post(
        f"{AUTH}/verify-email/resend", json={"email": verified["email"]}
    )
    assert already.status_code == 200
    assert already.json() == unknown.json()  # identical response
    assert sent_emails == []  # no pointless mail to a verified account


# --------------------------------------------------------------------------- #
# Password reset proves ownership
# --------------------------------------------------------------------------- #
async def test_password_reset_also_verifies_the_address(
    client: AsyncClient, signup, login
) -> None:
    """Receiving the reset link proves control of the mailbox."""
    account = await signup(verify=False)

    reset_token = (
        await client.post(
            f"{AUTH}/password-reset/request", json={"email": account["email"]}
        )
    ).json()["reset_token"]
    confirm = await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": reset_token, "new_password": "brandnewpass1"},
    )
    assert confirm.status_code == 200

    body = await login(account["email"], "brandnewpass1")
    assert body["user"]["is_verified"] is True


async def test_password_reset_sends_a_change_notification(
    client: AsyncClient, signup, sent_emails
) -> None:
    account = await signup()
    reset_token = (
        await client.post(
            f"{AUTH}/password-reset/request", json={"email": account["email"]}
        )
    ).json()["reset_token"]
    assert sent_emails[-1]["tag"] == "password_reset"

    await client.post(
        f"{AUTH}/password-reset/confirm",
        json={"token": reset_token, "new_password": "brandnewpass1"},
    )
    assert sent_emails[-1]["tag"] == "password_changed"
    assert account["email"] in sent_emails[-1]["html"]


# --------------------------------------------------------------------------- #
# Changing a password while signed in
# --------------------------------------------------------------------------- #
async def test_change_password_rotates_and_revokes_sessions(
    client: AsyncClient, signup, login, sent_emails
) -> None:
    account = await signup(password="oldpassword1")
    tokens = await login(account["email"], "oldpassword1")
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    await asyncio.sleep(1.1)  # cross the 1-second iat boundary

    resp = await client.post(
        f"{AUTH}/password/change",
        headers=auth,
        json={"current_password": "oldpassword1", "new_password": "newpassword1"},
    )
    assert resp.status_code == 200

    # Every session is gone, including the caller's.
    assert (await client.get(f"{AUTH}/me", headers=auth)).status_code == 401
    assert sent_emails[-1]["tag"] == "password_changed"

    # Only the new password works.
    assert (
        await client.post(
            f"{AUTH}/login",
            json={"email": account["email"], "password": "oldpassword1"},
        )
    ).status_code == 401
    await login(account["email"], "newpassword1")


async def test_change_password_requires_the_current_one(
    client: AsyncClient, signup, login
) -> None:
    account = await signup()
    tokens = await login(account["email"], account["password"])
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    wrong = await client.post(
        f"{AUTH}/password/change",
        headers=auth,
        json={"current_password": "notmypassword", "new_password": "newpassword1"},
    )
    assert wrong.status_code == 400

    unchanged = await client.post(
        f"{AUTH}/password/change",
        headers=auth,
        json={
            "current_password": account["password"],
            "new_password": account["password"],
        },
    )
    assert unchanged.status_code == 400


async def test_change_password_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post(
        f"{AUTH}/password/change",
        json={"current_password": "whatever1", "new_password": "newpassword1"},
    )
    assert resp.status_code == 401
