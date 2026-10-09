"""User file uploads attached to chat messages.

An attachment is uploaded on its own (so the browser can show a preview and a
progress bar before anything is sent), then referenced by id when the message
is submitted. Text is extracted once at upload time rather than on every turn,
and the extracted copy is what reaches the model.
"""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class AttachmentKind(str, enum.Enum):
    """How the file reaches the model."""

    document = "document"  # text was extracted and goes into the prompt
    image = "image"  # sent to the model as an image, needs a vision model


class ExtractionStatus(str, enum.Enum):
    ok = "ok"
    empty = "empty"  # parsed fine but held no text (e.g. a scanned PDF)
    truncated = "truncated"
    unsupported = "unsupported"
    failed = "failed"


class Attachment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "attachments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Set when the attachment is first used in a message. Null means uploaded
    # but never sent, which is what the orphan cleanup looks for.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Opaque path within the storage backend; never built from user input.
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)

    kind: Mapped[AttachmentKind] = mapped_column(
        SAEnum(AttachmentKind, name="attachment_kind", native_enum=True), nullable=False
    )
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        SAEnum(ExtractionStatus, name="extraction_status", native_enum=True),
        nullable=False,
    )
    # Sanitised text handed to the model. Null for images.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # page_count, original_content_type, truncation details, etc.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    user: Mapped["User"] = relationship()  # noqa: F821

    __table_args__ = (Index("ix_attachments_user_created", "user_id", "created_at"),)
