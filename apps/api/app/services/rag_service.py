"""Pluggable retrieval service for Ask SoakinGarri.

The 2,000+ PDF corpus is still moving through the ingest pipeline, so this
service ships **stubbed**: ``retrieve_context`` returns ``[]`` and the API
contract carries ``sources: []`` until the vector index is loaded.

Activation is configuration, not code:
  * ``RAG_ENABLED=true``  -> real pgvector cosine search over ``history_chunks``
  * ``RAG_DEV_MOCK=true`` (development only) -> one canned chunk, so the whole
    templating/citation/UI pipeline can be exercised without an index.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.ask_retrieval_policy import sanitize_text, validate_scope


@dataclass
class RetrievedChunk:
    """One retrieval hit, already sanitized and safe to template."""

    title: str
    snippet: str
    content: str
    category: str = "african_history"
    source_url: str | None = None
    score: float = 0.0


_DEV_MOCK_CHUNK = RetrievedChunk(
    title="Dev Mock — The Mali Empire",
    snippet=(
        "The Mali Empire (c. 1235–1670) rose under Sundiata Keita after the "
        "Battle of Kirina and grew wealthy on trans-Saharan gold and salt trade."
    ),
    content=(
        "The Mali Empire (c. 1235–1670) rose under Sundiata Keita after the "
        "Battle of Kirina and grew wealthy on trans-Saharan gold and salt "
        "trade. Under Mansa Musa its cities of Timbuktu and Djenné became "
        "centres of Islamic scholarship."
    ),
    category="dev_mock",
    score=0.99,
)


class RAGService:
    async def retrieve_context(
        self,
        query: str,
        *,
        db: AsyncSession | None = None,
        scope: str = "ask_soakingarri",
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Return the top-k chunks for ``query``, or ``[]`` while stubbed."""
        validate_scope(scope)
        top_k = top_k or settings.RAG_TOP_K

        if settings.RAG_ENABLED and db is not None:
            return await self._retrieve_pgvector(db, query, top_k)

        if settings.ENVIRONMENT == "development" and settings.RAG_DEV_MOCK:
            return [_DEV_MOCK_CHUNK]

        # Stub: vector index not loaded yet.
        return []

    async def _retrieve_pgvector(
        self, db: AsyncSession, query: str, top_k: int
    ) -> list[RetrievedChunk]:
        """Cosine search over the embedded history corpus (real backend)."""
        # Imported lazily so the stubbed path never touches pgvector/boto3.
        from app.models.ask import HistoryChunk, HistoryDocument
        from app.services.ai_service import ai_service

        embedding = (await ai_service.embed([query]))[0]
        distance = HistoryChunk.embedding.cosine_distance(embedding).label("distance")
        stmt = (
            select(HistoryChunk, HistoryDocument, distance)
            .join(HistoryDocument, HistoryChunk.document_id == HistoryDocument.id)
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await db.execute(stmt)).all()
        return [
            RetrievedChunk(
                title=doc.title,
                snippet=sanitize_text(chunk.content)[:400],
                content=sanitize_text(chunk.content),
                category=doc.meta.get("category", "african_history"),
                source_url=doc.meta.get("source_url"),
                score=round(1.0 - float(dist), 4),
            )
            for chunk, doc, dist in rows
        ]

    def format_context(self, chunks: list[RetrievedChunk]) -> str:
        """Render chunks as numbered, citable source blocks (empty str if none)."""
        return "\n\n".join(
            f"[Source {i + 1}: {sanitize_text(c.title)} | category: "
            f"{sanitize_text(c.category)}]\n{sanitize_text(c.content)}"
            for i, c in enumerate(chunks)
        )


rag_service = RAGService()
