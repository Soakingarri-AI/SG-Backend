"""Attachments flowing through an Ask turn.

Covers what the model actually receives: extracted document text fenced inside
``<user_attachments>``, images as multimodal parts, and the persistence that
lets the frontend re-render an old turn with its files.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

import pytest
from httpx import AsyncClient

from app.services.ai_service import AIResult, Usage, ai_service
from tests.files import pdf_bytes, png_bytes

ASK = "/api/v1/ask"
UPLOADS = "/api/v1/uploads"

_OPEN_RE = re.compile(r"<\s*user_attachments\s*>", re.IGNORECASE)
_CLOSE_RE = re.compile(r"</\s*user_attachments\s*>", re.IGNORECASE)


@dataclass
class AICapture:
    calls: list[dict] = field(default_factory=list)

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> AIResult:
        self.calls.append({"system": system, "messages": messages})
        return AIResult(text="Mocked answer.", usage=Usage(3, 4))

    @property
    def last_content(self):
        return self.calls[-1]["messages"][-1]["content"]

    @property
    def last_text(self) -> str:
        content = self.last_content
        if isinstance(content, str):
            return content
        return "\n".join(p.get("text", "") for p in content if p["type"] == "text")


@pytest.fixture
def mock_ai(monkeypatch: pytest.MonkeyPatch) -> AICapture:
    capture = AICapture()
    monkeypatch.setattr(ai_service, "complete", capture.complete)
    return capture


async def _upload(client: AsyncClient, headers: dict, data: bytes, name: str) -> str:
    resp = await client.post(
        UPLOADS, headers=headers, files={"file": (name, data, "application/octet-stream")}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
async def test_document_text_reaches_the_model(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    attachment_id = await _upload(client, headers, pdf_bytes(), "history.pdf")

    resp = await client.post(
        ASK,
        headers=headers,
        json={"prompt": "Summarise this", "attachment_ids": [attachment_id]},
    )
    assert resp.status_code == 200, resp.text

    sent = mock_ai.last_text
    assert "Mansa Musa" in sent
    assert "history.pdf" in sent  # labelled so the model can refer to it
    # Fenced exactly once, and the content sits inside the fence.
    assert len(_OPEN_RE.findall(sent)) == 1 and len(_CLOSE_RE.findall(sent)) == 1
    assert _OPEN_RE.search(sent).end() < sent.index("Mansa Musa") < _CLOSE_RE.search(sent).start()

    body = resp.json()
    assert [a["filename"] for a in body["attachments"]] == ["history.pdf"]
    assert body["attachments"][0]["kind"] == "document"


async def test_attachment_is_recorded_on_the_message(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    """So reopening a conversation can re-render its file chips."""
    headers = await auth_headers()
    attachment_id = await _upload(client, headers, b"Essay body", "essay.txt")

    session_id = (
        await client.post(
            ASK,
            headers=headers,
            json={"prompt": "Mark this", "attachment_ids": [attachment_id]},
        )
    ).json()["session_id"]

    detail = (await client.get(f"{ASK}/sessions/{session_id}", headers=headers)).json()
    attached = detail["messages"][0]["meta"]["attachments"]
    assert attached[0]["id"] == attachment_id
    assert attached[0]["filename"] == "essay.txt"

    # And the attachment is now bound to that session.
    meta = (await client.get(f"{UPLOADS}/{attachment_id}", headers=headers)).json()
    assert meta["session_id"] == session_id


async def test_turn_without_attachments_sends_plain_text(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    """No attachment block, and content stays a plain string."""
    headers = await auth_headers()
    await client.post(ASK, headers=headers, json={"prompt": "Who was Sundiata?"})

    assert isinstance(mock_ai.last_content, str)
    assert not _OPEN_RE.search(mock_ai.last_content)


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #
async def test_image_is_sent_as_a_vision_part(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    attachment_id = await _upload(client, headers, png_bytes(), "question.png")

    resp = await client.post(
        ASK,
        headers=headers,
        json={"prompt": "Solve what you see", "attachment_ids": [attachment_id]},
    )
    assert resp.status_code == 200

    content = mock_ai.last_content
    assert isinstance(content, list)  # multimodal turn
    images = [p for p in content if p["type"] == "image"]
    assert len(images) == 1
    assert images[0]["media_type"] == "image/png"
    assert images[0]["data"]  # base64 payload
    assert resp.json()["attachments"][0]["kind"] == "image"


async def test_provider_translation_for_images(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    """The neutral image part converts to each provider's own wire format."""
    from app.services.ai_service import _to_anthropic_messages, _to_openai_messages

    neutral = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image", "media_type": "image/png", "data": "QUJD"},
            ],
        }
    ]

    openai_part = _to_openai_messages(neutral)[0]["content"][1]
    assert openai_part["type"] == "image_url"
    assert openai_part["image_url"]["url"] == "data:image/png;base64,QUJD"

    anthropic_part = _to_anthropic_messages(neutral)[0]["content"][1]
    assert anthropic_part["type"] == "image"
    assert anthropic_part["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "QUJD",
    }

    # Plain string messages pass through both adapters untouched.
    plain = [{"role": "user", "content": "hello"}]
    assert _to_openai_messages(plain) == plain
    assert _to_anthropic_messages(plain) == plain


# --------------------------------------------------------------------------- #
# Ownership and limits
# --------------------------------------------------------------------------- #
async def test_cannot_attach_another_users_file(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    owner = await auth_headers()
    intruder = await auth_headers()
    attachment_id = await _upload(client, owner, b"private", "private.txt")

    resp = await client.post(
        ASK,
        headers=intruder,
        json={"prompt": "Read this", "attachment_ids": [attachment_id]},
    )
    assert resp.status_code == 403
    assert not mock_ai.calls  # rejected before the model was called


async def test_unknown_attachment_is_404(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    resp = await client.post(
        ASK,
        headers=headers,
        json={"prompt": "Read this", "attachment_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404


async def test_attachment_cannot_cross_sessions(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    attachment_id = await _upload(client, headers, b"Shared doc", "doc.txt")

    first = await client.post(
        ASK, headers=headers, json={"prompt": "One", "attachment_ids": [attachment_id]}
    )
    assert first.status_code == 200

    # Re-using it in the same session is fine...
    same = await client.post(
        ASK,
        headers=headers,
        json={
            "prompt": "Again",
            "session_id": first.json()["session_id"],
            "attachment_ids": [attachment_id],
        },
    )
    assert same.status_code == 200

    # ...but it may not leak into a different conversation.
    other = await client.post(
        ASK, headers=headers, json={"prompt": "Elsewhere", "attachment_ids": [attachment_id]}
    )
    assert other.status_code == 409


async def test_too_many_attachments_rejected(
    client: AsyncClient, auth_headers, mock_ai: AICapture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPLOAD_MAX_PER_MESSAGE", 2)
    headers = await auth_headers()
    ids = [await _upload(client, headers, f"file {i}".encode(), f"f{i}.txt") for i in range(3)]

    resp = await client.post(
        ASK, headers=headers, json={"prompt": "All of them", "attachment_ids": ids}
    )
    assert resp.status_code == 422
    assert not mock_ai.calls


# --------------------------------------------------------------------------- #
# Unreadable files are reported, not silently dropped
# --------------------------------------------------------------------------- #
async def test_unreadable_document_is_flagged_to_the_model(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    """A scanned PDF yields no text; the model must be told rather than guess."""
    headers = await auth_headers()
    attachment_id = await _upload(client, headers, pdf_bytes(text=" "), "scan.pdf")

    resp = await client.post(
        ASK, headers=headers, json={"prompt": "Read it", "attachment_ids": [attachment_id]}
    )
    assert resp.status_code == 200

    sent = mock_ai.last_text
    assert "scan.pdf" in sent
    assert "no text" in sent.lower() or "could not be read" in sent.lower()
