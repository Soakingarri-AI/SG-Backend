"""Ask SoakinGarri API tests.

Covers session lifecycle, turn persistence, the stubbed-RAG contract
(``sources: []``), auth enforcement (401/403/404), and learning-mode routing.
``ai_service.complete`` is monkeypatched — no live LLM calls.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from httpx import AsyncClient

from app.services.ai_service import AIResult, Usage, ai_service

ASK = "/api/v1/ask"




@dataclass
class AICapture:
    """Records every ``ai_service.complete`` call made during a test."""

    calls: list[dict] = field(default_factory=list)
    answer: str = "Mocked tutor answer."

    async def complete(self, *, system: str, messages: list[dict], **kwargs) -> AIResult:
        self.calls.append({"system": system, "messages": messages})
        return AIResult(text=self.answer, usage=Usage(11, 22))


@pytest.fixture
def mock_ai(monkeypatch: pytest.MonkeyPatch) -> AICapture:
    capture = AICapture()
    monkeypatch.setattr(ai_service, "complete", capture.complete)
    return capture


# --------------------------------------------------------------------------- #
# Session creation + persistence + stubbed-RAG contract
# --------------------------------------------------------------------------- #
async def test_ask_creates_session_and_persists_both_turns(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    prompt = "Who was Mansa Musa and why does his hajj matter?"

    resp = await client.post(ASK, json={"prompt": prompt}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"] == mock_ai.answer
    assert body["learning_mode"] == "normal"
    # RAG is stubbed until the vector index loads: contract-valid empty list.
    assert body["sources"] == []
    uuid.UUID(body["session_id"])
    uuid.UUID(body["message_id"])

    # Session listed with a title derived from the first prompt.
    resp = await client.get(f"{ASK}/sessions", headers=headers)
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == body["session_id"]
    assert sessions[0]["title"] == prompt[:80]

    # Both turns persisted, user first.
    resp = await client.get(f"{ASK}/sessions/{body['session_id']}", headers=headers)
    assert resp.status_code == 200
    detail = resp.json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == prompt
    assert detail["messages"][1]["content"] == mock_ai.answer
    meta = detail["messages"][1]["meta"]
    assert meta["sources"] == []
    assert meta["learning_mode"] == "normal"
    assert "model" in meta


async def test_multi_turn_replays_history_into_the_model(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    first = "Explain the Benin Bronzes."
    r1 = await client.post(ASK, json={"prompt": first}, headers=headers)
    session_id = r1.json()["session_id"]

    r2 = await client.post(
        ASK, json={"prompt": "And where are they today?", "session_id": session_id},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == session_id

    # Second call saw the first turn as alternating history + the new question.
    messages = mock_ai.calls[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == first
    assert messages[1]["content"] == mock_ai.answer
    assert "And where are they today?" in messages[2]["content"]

    detail = (await client.get(f"{ASK}/sessions/{session_id}", headers=headers)).json()
    assert [m["role"] for m in detail["messages"]] == [
        "user", "assistant", "user", "assistant",
    ]


async def test_sessions_ordered_by_recency(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    s1 = (await client.post(ASK, json={"prompt": "First topic"}, headers=headers)).json()
    s2 = (await client.post(ASK, json={"prompt": "Second topic"}, headers=headers)).json()
    assert s1["session_id"] != s2["session_id"]

    # Continue the first session so it becomes the most recently active.
    await client.post(
        ASK, json={"prompt": "More on the first", "session_id": s1["session_id"]},
        headers=headers,
    )
    ids = [s["id"] for s in (await client.get(f"{ASK}/sessions", headers=headers)).json()]
    assert ids == [s1["session_id"], s2["session_id"]]


# --------------------------------------------------------------------------- #
# Learning modes
# --------------------------------------------------------------------------- #
async def test_learning_modes_select_distinct_system_styles(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    expectations = {
        "beginner": "Beginner mode.",
        "normal": "Normal mode.",
        "advanced": "Advanced mode.",
    }
    for mode, marker in expectations.items():
        resp = await client.post(
            ASK, json={"prompt": f"Explain photosynthesis ({mode})", "learning_mode": mode},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["learning_mode"] == mode
        assert marker in mock_ai.calls[-1]["system"]

    # The three system prompts genuinely differ.
    systems = {call["system"] for call in mock_ai.calls}
    assert len(systems) == 3


async def test_mode_switch_updates_session_meta(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    r1 = await client.post(
        ASK, json={"prompt": "Start easy", "learning_mode": "beginner"}, headers=headers
    )
    sid = r1.json()["session_id"]
    await client.post(
        ASK,
        json={"prompt": "Now go deep", "learning_mode": "advanced", "session_id": sid},
        headers=headers,
    )
    detail = (await client.get(f"{ASK}/sessions/{sid}", headers=headers)).json()
    assert detail["learning_mode"] == "advanced"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
async def test_prompt_length_bounds(client: AsyncClient, auth_headers, mock_ai: AICapture) -> None:
    headers = await auth_headers()
    assert (
        await client.post(ASK, json={"prompt": ""}, headers=headers)
    ).status_code == 422
    assert (
        await client.post(ASK, json={"prompt": "x" * 4001}, headers=headers)
    ).status_code == 422
    assert not mock_ai.calls  # rejected before any LLM call


async def test_whitespace_only_prompt_rejected(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    resp = await client.post(ASK, json={"prompt": " ​ ​ "}, headers=headers)
    assert resp.status_code == 422
    assert not mock_ai.calls


# --------------------------------------------------------------------------- #
# AuthN / AuthZ
# --------------------------------------------------------------------------- #
async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.post(ASK, json={"prompt": "hi"})).status_code == 401
    assert (await client.get(f"{ASK}/sessions")).status_code == 401
    sid = uuid.uuid4()
    assert (await client.get(f"{ASK}/sessions/{sid}")).status_code == 401
    assert (await client.delete(f"{ASK}/sessions/{sid}")).status_code == 401


async def test_foreign_session_is_forbidden(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    owner = await auth_headers()
    intruder = await auth_headers()
    sid = (await client.post(ASK, json={"prompt": "mine"}, headers=owner)).json()[
        "session_id"
    ]

    assert (
        await client.get(f"{ASK}/sessions/{sid}", headers=intruder)
    ).status_code == 403
    assert (
        await client.delete(f"{ASK}/sessions/{sid}", headers=intruder)
    ).status_code == 403
    assert (
        await client.post(
            ASK, json={"prompt": "hijack", "session_id": sid}, headers=intruder
        )
    ).status_code == 403

    # Owner remains unaffected.
    assert (await client.get(f"{ASK}/sessions/{sid}", headers=owner)).status_code == 200


async def test_unknown_session_is_404(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    ghost = uuid.uuid4()
    assert (await client.get(f"{ASK}/sessions/{ghost}", headers=headers)).status_code == 404
    assert (await client.delete(f"{ASK}/sessions/{ghost}", headers=headers)).status_code == 404
    assert (
        await client.post(
            ASK, json={"prompt": "hi", "session_id": str(ghost)}, headers=headers
        )
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Deletion
# --------------------------------------------------------------------------- #
async def test_delete_removes_session_and_messages(
    client: AsyncClient, auth_headers, mock_ai: AICapture
) -> None:
    headers = await auth_headers()
    sid = (await client.post(ASK, json={"prompt": "temp"}, headers=headers)).json()[
        "session_id"
    ]

    resp = await client.delete(f"{ASK}/sessions/{sid}", headers=headers)
    assert resp.status_code == 204

    assert (await client.get(f"{ASK}/sessions/{sid}", headers=headers)).status_code == 404
    assert (await client.get(f"{ASK}/sessions", headers=headers)).json() == []

    # Messages are gone at the DB level too (FK cascade).
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal
    from app.models.chat_session import ChatMessage

    async with AsyncSessionLocal() as db:
        count = (
            await db.execute(
                select(func.count()).select_from(ChatMessage).where(
                    ChatMessage.session_id == uuid.UUID(sid)
                )
            )
        ).scalar_one()
    assert count == 0
