"""Pluggable retrieval service for Ask SoakinGarri.

With ``RAG_ENABLED=false`` this service is **stubbed**: ``retrieve_context``
returns ``[]`` and the API contract carries ``sources: []``.

Activation is configuration, not code:
  * ``RAG_ENABLED=true`` + ``RAG_BACKEND=bedrock_kb`` -> the Bedrock Knowledge
    Base over the S3 PDF corpus (``BEDROCK_KB_ID``)
  * ``RAG_ENABLED=true`` + ``RAG_BACKEND=pgvector`` -> cosine search over
    ``history_chunks``
  * ``RAG_DEV_MOCK=true`` (development only) -> one canned chunk, so the whole
    templating/citation/UI pipeline can be exercised without an index.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.ask_retrieval_policy import sanitize_text, validate_scope

logger = logging.getLogger(__name__)

_KB_PAGE_KEY = "x-amz-bedrock-kb-document-page-number"


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
    def __init__(self) -> None:
        self._kb_client: Any = None

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

        if settings.RAG_ENABLED and settings.RAG_BACKEND == "bedrock_kb":
            return await self._retrieve_bedrock_kb(query, top_k)
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

    async def _retrieve_bedrock_kb(self, query: str, top_k: int) -> list[RetrievedChunk]:
        """Semantic search in the Bedrock Knowledge Base (real backend).

        A failed search degrades to no sources rather than failing the
        question: the tutor can still answer, just without citations.
        """
        try:
            response = await asyncio.to_thread(
                self._kb().retrieve,
                knowledgeBaseId=settings.BEDROCK_KB_ID,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": top_k}
                },
            )
        except Exception:
            logger.exception("Knowledge Base retrieve failed; answering without sources")
            return []
        chunks = [_kb_chunk(hit) for hit in response.get("retrievalResults", [])]
        return [c for c in chunks if c.score >= settings.RAG_MIN_SCORE]

    def _kb(self) -> Any:
        if self._kb_client is None:
            # Imported lazily so the stubbed path never touches boto3.
            import boto3
            from botocore.config import Config

            self._kb_client = boto3.client(
                "bedrock-agent-runtime",
                region_name=settings.BEDROCK_REGION,
                config=Config(
                    connect_timeout=5,
                    read_timeout=20,
                    retries={"mode": "standard", "total_max_attempts": 3},
                ),
            )
        return self._kb_client

    def format_context(self, chunks: list[RetrievedChunk]) -> str:
        """Render chunks as numbered, citable source blocks (empty str if none)."""
        return "\n\n".join(
            f"[Source {i + 1}: {sanitize_text(c.title)} | category: "
            f"{sanitize_text(c.category)}]\n{sanitize_text(c.content)}"
            for i, c in enumerate(chunks)
        )


def _kb_chunk(hit: dict[str, Any]) -> RetrievedChunk:
    """Map one Retrieve result to a chunk; metadata comes from the PDF's sidecar
    (rag_collector/pdf_downloader.py), so it is as untrusted as the text."""
    meta = hit.get("metadata") or {}
    content = sanitize_text(str(hit.get("content", {}).get("text", "")))
    title = sanitize_text(str(meta.get("title") or "Untitled document"))
    page = meta.get(_KB_PAGE_KEY)
    if isinstance(page, (int, float)) and page > 0:
        title = f"{title}, p. {int(page)}"
    url = str(meta.get("source_url") or "")
    return RetrievedChunk(
        title=title,
        snippet=content[:400],
        content=content,
        category=sanitize_text(str(meta.get("concept") or "african_history")),
        source_url=url if url.startswith(("https://", "http://")) else None,
        score=round(float(hit.get("score") or 0.0), 4),
    )


rag_service = RAGService()
