"""Central polymorphic chat/session store shared by all subdomains.

Every tool writes its conversational history here, discriminated by the
``tool_type`` ENUM. A per-tool ``meta`` JSONB column carries tool-specific
context (learning mode, exam id, persona set, etc.) without schema forks.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class ToolType(str, enum.Enum):
    """Discriminator separating history across the six subdomains."""

    ask = "ask"
    examflow = "examflow"
    afrosimulator = "afrosimulator"
    memes = "memes"
    infiniteparts = "infiniteparts"
    factorizer = "factorizer"


class MessageRole(str, enum.Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ChatSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tool_type: Mapped[ToolType] = mapped_column(
        SAEnum(ToolType, name="tool_type", native_enum=True), index=True, nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    user: Mapped["User"] = relationship(back_populates="chat_sessions")  # noqa: F821
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )

    __table_args__ = (
        Index("ix_chat_sessions_user_tool", "user_id", "tool_type"),
    )


class ChatMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    # Denormalized owner id so per-user message queries/audits skip the session
    # join. Nullable: rows written before this column existed have no value.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, name="message_role", native_enum=True), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Citations, token usage, tool payloads, etc.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(default=0, nullable=False)

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
