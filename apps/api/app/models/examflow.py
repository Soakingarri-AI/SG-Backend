"""ExamFlow — tagged MCQ bank + generated exam sessions.

Questions are tagged by exam board, subject, and year so the mixer algorithm can
compose non-overlapping custom tests.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class ExamBoard(str, enum.Enum):
    waec = "WAEC"
    jamb = "JAMB"
    neco = "NECO"
    common_entrance = "COMMON_ENTRANCE"


class ExamQuestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "exam_questions"

    board: Mapped[ExamBoard] = mapped_column(
        SAEnum(ExamBoard, name="exam_board", native_enum=True), index=True
    )
    subject: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200), nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)  # 1..5

    stem: Mapped[str] = mapped_column(Text, nullable=False)
    # options: [{"key": "A", "text": "..."}, ...]
    options: Mapped[list] = mapped_column(JSONB, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(4), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class ExamSession(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "exam_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    board: Mapped[ExamBoard] = mapped_column(SAEnum(ExamBoard, name="exam_board"))
    subject: Mapped[str] = mapped_column(String(120))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    is_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    questions: Mapped[list["ExamSessionQuestion"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ExamSessionQuestion.position",
    )


class ExamSessionQuestion(UUIDMixin, Base):
    """Join row: a question placed in a session, plus the user's answer."""

    __tablename__ = "exam_session_questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("exam_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("exam_questions.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_option: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    session: Mapped["ExamSession"] = relationship(back_populates="questions")
    question: Mapped["ExamQuestion"] = relationship()
