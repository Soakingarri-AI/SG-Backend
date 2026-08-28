"""Ask SoakinGarri orchestration.

One entry point, ``answer()``, runs the full turn:

  1. fetch-or-create the ``ChatSession`` (strict ownership),
  2. load the last N messages as conversation memory,
  3. retrieve corpus context via :mod:`rag_service` (stubbed -> ``[]``),
  4. wrap context + question with ``ask_context_template.md`` so retrieved
     text stays isolated from instructions,
  5. call the centralized :mod:`ai_service`,
  6. persist both turns to the shared chat store (sources/mode/model in meta),
  7. return the :class:`AskResponse` contract.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_session import ChatMessage, ChatSession, MessageRole, ToolType
from app.prompts import load_prompt
from app.schemas.ask import AskRequest, AskResponse, AskSource, LearningMode
from app.services.ai_service import ai_service
from app.services.ask_retrieval_policy import PolicyViolation, validate_prompt
from app.services.rag_service import RetrievedChunk, rag_service

_MODE_STYLE: dict[LearningMode, str] = {
    LearningMode.beginner: (
        "**Beginner mode.** Explain from first principles for a curious newcomer: "
        "short sentences, everyday analogies, a foundational breakdown of the idea, "
        "and no unexplained technical jargon — define every term you introduce."
    ),
    LearningMode.normal: (
        "**Normal mode.** Standard educational depth: a balanced, well-structured "
        "explanation with clear step-by-step reasoning, suitable for a motivated "
        "secondary-school or early-university learner."
    ),
    LearningMode.advanced: (
        "**Advanced mode.** Academic and technical depth: precise dates, actors, and "
        "mechanisms, nuanced historiographical or technical debate where it exists, "
        "and rigorous, source-aware argumentation."
    ),
}

_TITLE_MAX = 80


def build_system_prompt(mode: LearningMode) -> str:
    return load_prompt("ask_soakingarri_system").format(
        learning_mode_style=_MODE_STYLE[mode]
    )


def build_user_message(question: str, context: str) -> str:
    """Wrap the (already sanitized) question and context in the isolation template."""
    if not context:
        context = "(no sources retrieved for this question)"
    return load_prompt("ask_context_template").format(
        context=context, question=question
    )


class AskService:
    async def answer(
        self, db: AsyncSession, user_id: uuid.UUID, payload: AskRequest
    ) -> AskResponse:
        try:
            prompt = validate_prompt(
                payload.prompt, max_tokens=settings.ASK_MAX_PROMPT_TOKENS
            )
        except PolicyViolation as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session = await self._get_or_create_session(
            db, user_id, payload.session_id, payload.learning_mode, prompt
        )
        history = await self._load_history(db, session.id)

        chunks = await rag_service.retrieve_context(prompt, db=db)
        context = rag_service.format_context(chunks)
        sources = [
            AskSource(
                title=c.title,
                source_url=c.source_url,
                snippet=c.snippet,
                category=c.category,
            )
            for c in chunks
        ]

        result = await ai_service.complete(
            system=build_system_prompt(payload.learning_mode),
            messages=[*history, {"role": "user", "content": build_user_message(prompt, context)}],
        )

        db.add(
            ChatMessage(
                session_id=session.id,
                user_id=user_id,
                role=MessageRole.user,
                content=prompt,
                meta={"learning_mode": payload.learning_mode.value},
            )
        )
        assistant_msg = ChatMessage(
            session_id=session.id,
            user_id=user_id,
            role=MessageRole.assistant,
            content=result.text,
            meta={
                "learning_mode": payload.learning_mode.value,
                "sources": [s.model_dump() for s in sources],
                "model": ai_service.model,
            },
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
        db.add(assistant_msg)
        # Touch the session so ``updated_at`` reflects the newest turn and the
        # stored mode follows the latest request. ``updated_at`` is set
        # explicitly because an equal ``meta`` dict would not mark the row dirty.
        session.meta = {**session.meta, "learning_mode": payload.learning_mode.value}
        session.updated_at = datetime.now(UTC)
        await db.flush()

        return AskResponse(
            session_id=session.id,
            message_id=assistant_msg.id,
            answer=result.text,
            learning_mode=payload.learning_mode,
            sources=sources,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    async def _get_or_create_session(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: uuid.UUID | None,
        mode: LearningMode,
        first_prompt: str,
    ) -> ChatSession:
        if session_id is not None:
            session = await get_owned_session(db, session_id, user_id)
            return session
        session = ChatSession(
            user_id=user_id,
            tool_type=ToolType.ask,
            title=first_prompt[:_TITLE_MAX],
            meta={"learning_mode": mode.value},
        )
        db.add(session)
        await db.flush()
        return session

    async def _load_history(
        self, db: AsyncSession, session_id: uuid.UUID
    ) -> list[dict[str, str]]:
        """Last N user/assistant messages, oldest first, as chat turns."""
        # Both messages of a turn share one transaction timestamp, so tie-break
        # on role (native enum order puts ``user`` before ``assistant``) to keep
        # replayed turns strictly alternating.
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role.in_([MessageRole.user, MessageRole.assistant]),
            )
            .order_by(ChatMessage.created_at.desc(), ChatMessage.role.desc())
            .limit(settings.ASK_HISTORY_MESSAGES)
        )
        rows = list((await db.execute(stmt)).scalars())
        rows.reverse()
        return [{"role": m.role.value, "content": m.content} for m in rows]


async def get_owned_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> ChatSession:
    """Load an Ask session; 404 if absent, 403 if owned by someone else."""
    session = await db.get(ChatSession, session_id)
    if session is None or session.tool_type != ToolType.ask:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    if session.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this session",
        )
    return session


ask_service = AskService()


__all__ = [
    "AskService",
    "ask_service",
    "build_system_prompt",
    "build_user_message",
    "get_owned_session",
    "RetrievedChunk",
]
