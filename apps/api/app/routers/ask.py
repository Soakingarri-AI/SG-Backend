"""Ask SoakinGarri — RAG teaching assistant router.

Flow: embed the query -> pgvector cosine search over African-history chunks ->
build a cited context -> ask the LLM in the requested Learning Mode -> persist to
the polymorphic chat store -> return answer + citation panels.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.ask import HistoryChunk, HistoryDocument
from app.models.chat_session import ChatMessage, ChatSession, MessageRole, ToolType
from app.models.user import User
from app.schemas.modules import AskRequest, AskResponse, Citation, LearningMode
from app.services.ai_service import ai_service

router = APIRouter(prefix="/ask", tags=["ask"], dependencies=[Depends(rate_limit)])

_MODE_STYLE: dict[LearningMode, str] = {
    LearningMode.beginner: (
        "Explain simply for a curious beginner. Short sentences, everyday analogies, "
        "define any term you introduce."
    ),
    LearningMode.normal: "Explain clearly at a secondary-school level with balanced depth.",
    LearningMode.advanced: (
        "Explain rigorously for an advanced learner: nuance, historiographical debate, "
        "and precise dates/actors where relevant."
    ),
}

_TOP_K = 6


async def _retrieve(db: AsyncSession, query: str) -> list[tuple[HistoryChunk, HistoryDocument, float]]:
    embedding = (await ai_service.embed([query]))[0]
    distance = HistoryChunk.embedding.cosine_distance(embedding).label("distance")
    stmt = (
        select(HistoryChunk, HistoryDocument, distance)
        .join(HistoryDocument, HistoryChunk.document_id == HistoryDocument.id)
        .order_by(distance)
        .limit(_TOP_K)
    )
    rows = (await db.execute(stmt)).all()
    return [(c, d, 1.0 - float(dist)) for c, d, dist in rows]


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AskResponse:
    retrieved = await _retrieve(db, payload.query)

    citations = [
        Citation(
            document_title=doc.title,
            source_path=doc.source_path,
            snippet=chunk.content[:400],
            score=round(score, 4),
        )
        for chunk, doc, score in retrieved
    ]
    context = "\n\n".join(
        f"[Source {i + 1}: {doc.title}]\n{chunk.content}"
        for i, (chunk, doc, _) in enumerate(retrieved)
    )

    system = (
        "You are Ask SoakinGarri, an expert African-history teaching assistant. "
        f"{_MODE_STYLE[payload.mode]} "
        "Ground every claim in the provided sources and cite them inline as [Source N]. "
        "If the sources do not cover the question, say so plainly."
    )
    result = await ai_service.complete(
        system=system,
        messages=[
            {"role": "user", "content": f"Sources:\n{context}\n\nQuestion: {payload.query}"}
        ],
    )

    # Persist to the shared polymorphic chat store.
    session = await _get_or_create_session(db, user.id, payload.session_id, payload.mode)
    db.add(ChatMessage(session_id=session.id, role=MessageRole.user, content=payload.query))
    db.add(
        ChatMessage(
            session_id=session.id,
            role=MessageRole.assistant,
            content=result.text,
            meta={"citations": [c.model_dump() for c in citations]},
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
        )
    )

    return AskResponse(
        answer=result.text, citations=citations, session_id=session.id, mode=payload.mode
    )


async def _get_or_create_session(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID | None, mode: LearningMode
) -> ChatSession:
    if session_id:
        session = await db.get(ChatSession, session_id)
        if session and session.user_id == user_id:
            return session
    session = ChatSession(
        user_id=user_id, tool_type=ToolType.ask, meta={"mode": mode.value}
    )
    db.add(session)
    await db.flush()
    return session
