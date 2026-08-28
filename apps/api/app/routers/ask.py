"""Ask SoakinGarri — RAG teaching-assistant router (``/api/v1/ask``).

Thin HTTP layer: auth + rate limiting + ownership checks live here, while the
turn pipeline (history, retrieval, templating, persistence) lives in
``ask_service``. Retrieval is currently stubbed, so ``sources`` is ``[]``
until the vector index is loaded — the response contract is stable either way.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.deps import get_current_user
from app.models.chat_session import ChatMessage, ChatSession, ToolType
from app.models.user import User
from app.schemas.ask import (
    AskMessage,
    AskRequest,
    AskResponse,
    AskSessionDetailResponse,
    AskSessionListResponse,
    LearningMode,
)
from app.services.ask_service import ask_service, get_owned_session

router = APIRouter(prefix="/ask", tags=["ask"], dependencies=[Depends(rate_limit)])


@router.post("", response_model=AskResponse)
async def ask(
    payload: AskRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AskResponse:
    """Submit a question; creates or continues a chat session."""
    return await ask_service.answer(db, user.id, payload)


@router.get("/sessions", response_model=list[AskSessionListResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AskSessionListResponse]:
    """The caller's Ask sessions, most recently active first."""
    stmt = (
        select(ChatSession)
        .where(ChatSession.user_id == user.id, ChatSession.tool_type == ToolType.ask)
        .order_by(ChatSession.updated_at.desc())
    )
    sessions = (await db.execute(stmt)).scalars().all()
    return [AskSessionListResponse.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=AskSessionDetailResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AskSessionDetailResponse:
    """Full message history for one owned session (404 unknown, 403 foreign)."""
    session = await get_owned_session(db, session_id, user.id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.role.asc())
    )
    messages = (await db.execute(stmt)).scalars().all()
    return AskSessionDetailResponse(
        id=session.id,
        title=session.title,
        learning_mode=LearningMode(session.meta.get("learning_mode", "normal")),
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[AskMessage.model_validate(m) for m in messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    """Delete an owned session and, via FK cascade, all of its messages."""
    session = await get_owned_session(db, session_id, user.id)
    await db.delete(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
