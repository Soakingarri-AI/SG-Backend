"""Ask SoakinGarri — RAG document + chunk store with pgvector embeddings.

Raw and parsed African-history blocks in ``data/ask_soakingarri/african_history``
are ingested into ``HistoryDocument`` rows and split into embedded
``HistoryChunk`` rows for vector similarity search.
"""
from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin


class HistoryDocument(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "history_documents"

    source_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    era: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    chunks: Mapped[list["HistoryChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class HistoryChunk(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "history_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("history_documents.id", ondelete="CASCADE"),
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.EMBEDDING_DIM))

    document: Mapped["HistoryDocument"] = relationship(back_populates="chunks")

    __table_args__ = (
        # IVFFlat index for fast approximate nearest-neighbour cosine search.
        Index(
            "ix_history_chunks_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
