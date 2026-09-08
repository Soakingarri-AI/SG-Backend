"""Transactional email delivery via Resend.

Templates live as editable HTML in ``app/templates/email`` and are composed at
send time: a shared ``_layout.html`` supplies the chrome (logo, card, footer)
and each message supplies the inner content. ``string.Template`` does the
substitution so the templates' CSS braces need no escaping.

Sending never raises into a request. A failed or unconfigured send is logged
and reported as ``False`` — a user must still be able to register when the mail
provider is down, and local development needs no credentials at all.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from string import Template
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_RESEND_ENDPOINT = "https://api.resend.com/emails"


@lru_cache(maxsize=16)
def _load(name: str) -> Template:
    return Template((_TEMPLATE_DIR / f"{name}.html").read_text(encoding="utf-8"))


def _greeting(full_name: str | None) -> str:
    """'Hi Amina,' or a neutral fallback when we have no name."""
    first = (full_name or "").strip().split(" ")[0]
    return f"Hi {first}," if first else "Hi there,"


def render(
    template: str,
    *,
    subject: str,
    preheader: str,
    footer: str,
    **fields: str,
) -> str:
    """Render one email body: content template wrapped in the shared layout."""
    content = _load(template).substitute(**fields)
    return _load("_layout").substitute(
        subject=subject,
        preheader=preheader,
        footer=footer,
        content=content,
        logo_url=settings.EMAIL_LOGO_URL,
        year=str(datetime.now(UTC).year),
    )


async def send_email(
    *, to: str, subject: str, html: str, text: str, tag: str = "transactional"
) -> bool:
    """Dispatch one email. Returns whether it was accepted by the provider."""
    if not settings.email_enabled:
        # No credential configured (local dev): log enough to follow the flow.
        logger.warning(
            "Email sending disabled (no RESEND_API_KEY); would send %r to %s", subject, to
        )
        logger.info("Email body (text) for %s:\n%s", to, text)
        return False

    payload = {
        "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
        "tags": [{"name": "category", "value": tag}],
    }
    if settings.EMAIL_REPLY_TO:
        payload["reply_to"] = settings.EMAIL_REPLY_TO

    try:
        async with httpx.AsyncClient(timeout=settings.EMAIL_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _RESEND_ENDPOINT,
                json=payload,
                headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
    except httpx.HTTPError as exc:
        logger.error("Email transport error sending %r to %s: %s", subject, to, exc)
        return False

    if resp.status_code >= 400:
        # Resend puts the reason in the body; the API key is never echoed back.
        logger.error(
            "Resend rejected %r to %s: %s %s", subject, to, resp.status_code, resp.text
        )
        return False

    logger.info("Sent %r to %s (id=%s)", subject, to, resp.json().get("id"))
    return True


# --------------------------------------------------------------------------- #
# Link builders
# --------------------------------------------------------------------------- #
def _url(path: str, token: str | None = None) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}{path}?token={quote(token)}" if token else f"{base}{path}"


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
async def send_verification_email(
    *, email: str, full_name: str | None, token: str
) -> bool:
    hours = settings.EMAIL_VERIFICATION_EXPIRE_HOURS
    expiry = f"{hours} hour{'s' if hours != 1 else ''}"
    link = _url("/verify-email", token)
    # The sender name already says SoakinGarri AI, so subjects don't repeat it.
    subject = "Confirm your email address"

    html = render(
        "verify_email",
        subject=subject,
        preheader="One tap to activate your SoakinGarri AI account.",
        footer="You received this because someone signed up with this address.",
        greeting=_greeting(full_name),
        action_url=link,
        expiry=expiry,
    )
    text = (
        f"{_greeting(full_name)}\n\n"
        "Welcome to SoakinGarri AI. Confirm your email to activate your account:\n\n"
        f"{link}\n\n"
        f"This link expires in {expiry} and can be used once.\n"
        "If you didn't create an account, you can ignore this email.\n"
    )
    return await send_email(
        to=email, subject=subject, html=html, text=text, tag="verify_email"
    )


async def send_password_reset_email(
    *, email: str, full_name: str | None, token: str
) -> bool:
    minutes = settings.PASSWORD_RESET_EXPIRE_MINUTES
    expiry = f"{minutes} minute{'s' if minutes != 1 else ''}"
    link = _url("/reset-password", token)
    subject = "Reset your password"

    html = render(
        "password_reset",
        subject=subject,
        preheader="Choose a new password for your SoakinGarri AI account.",
        footer="If you didn't request this, no action is needed.",
        greeting=_greeting(full_name),
        email=email,
        action_url=link,
        expiry=expiry,
    )
    text = (
        f"{_greeting(full_name)}\n\n"
        f"We received a request to reset the password for {email}.\n"
        "Choose a new password here:\n\n"
        f"{link}\n\n"
        f"This link expires in {expiry} and can be used once.\n"
        "Didn't request this? Ignore this email and your password stays as it is.\n"
    )
    return await send_email(
        to=email, subject=subject, html=html, text=text, tag="password_reset"
    )


async def send_password_changed_email(*, email: str, full_name: str | None) -> bool:
    timestamp = datetime.now(UTC).strftime("%d %b %Y, %H:%M UTC")
    link = _url("/forgot-password")
    subject = "Your password was changed"

    html = render(
        "password_changed",
        subject=subject,
        preheader="Security notice: your account password just changed.",
        footer="This is a security notification about your account.",
        greeting=_greeting(full_name),
        email=email,
        action_url=link,
        timestamp=timestamp,
    )
    text = (
        f"{_greeting(full_name)}\n\n"
        f"The password for {email} was changed at {timestamp}, and all signed-in "
        "devices were signed out.\n\n"
        "If this wasn't you, reset your password immediately:\n"
        f"{link}\n"
    )
    return await send_email(
        to=email, subject=subject, html=html, text=text, tag="password_changed"
    )


async def send_welcome_email(*, email: str, full_name: str | None) -> bool:
    link = _url("/")
    subject = "Welcome to SoakinGarri AI"

    html = render(
        "welcome",
        subject=subject,
        preheader="Your account is live. Here's what you can do next.",
        footer="You're receiving this because you just verified your account.",
        greeting=_greeting(full_name),
        action_url=link,
    )
    text = (
        f"{_greeting(full_name)}\n\n"
        "Your email is confirmed and your account is live.\n\n"
        "- Ask SoakinGarri: an AI tutor for African history, culture, and STEM\n"
        "- ExamFlow: WAEC/JAMB/NECO practice exams with explanations\n"
        "- AfroSimulator, Memes, InfiniteParts and Factorizer\n\n"
        f"Start exploring: {link}\n"
    )
    return await send_email(
        to=email, subject=subject, html=html, text=text, tag="welcome"
    )
