"""Ingest the Ask SoakinGarri African-history corpus into pgvector.

Reads markdown/text files under ``data/ask_soakingarri/african_history``,
splits them into overlapping chunks, embeds each chunk via the AI service, and
upserts ``HistoryDocument`` + ``HistoryChunk`` rows.

Run with:  python -m scripts.ingest_history
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models.ask import HistoryChunk, HistoryDocument
from app.services.ai_service import ai_service

CORPUS_DIRS = [
    Path("/data/ask_soakingarri/african_history"),
    Path(__file__).resolve().parents[3] / "data" / "ask_soakingarri" / "african_history",
]
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


def _corpus_dir() -> Path:
    for d in CORPUS_DIRS:
        if d.exists():
            return d
    raise FileNotFoundError("African-history corpus directory not found")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        meta = {}
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        return meta, body.strip()
    return {}, text


def _chunk(text: str) -> list[str]:
    words = re.split(r"(\s+)", text)
    chunks, buf, size = [], [], 0
    for w in words:
        buf.append(w)
        size += len(w)
        if size >= CHUNK_SIZE:
            chunks.append("".join(buf).strip())
            # carry overlap
            overlap = "".join(buf)[-CHUNK_OVERLAP:]
            buf, size = [overlap], len(overlap)
    if "".join(buf).strip():
        chunks.append("".join(buf).strip())
    return [c for c in chunks if c]


async def main() -> None:
    corpus = _corpus_dir()
    files = sorted([*corpus.glob("*.md"), *corpus.glob("*.txt")])
    async with AsyncSessionLocal() as db:
        for path in files:
            raw = path.read_text(encoding="utf-8")
            meta, body = _parse_front_matter(raw)
            source_path = str(path)

            existing = await db.scalar(
                select(HistoryDocument).where(HistoryDocument.source_path == source_path)
            )
            if existing:
                await db.execute(
                    delete(HistoryChunk).where(HistoryChunk.document_id == existing.id)
                )
                doc = existing
                doc.title = meta.get("title", path.stem)
                doc.era = meta.get("era")
                doc.region = meta.get("region")
                doc.meta = meta
            else:
                doc = HistoryDocument(
                    source_path=source_path,
                    title=meta.get("title", path.stem),
                    era=meta.get("era"),
                    region=meta.get("region"),
                    meta=meta,
                )
                db.add(doc)
                await db.flush()

            chunks = _chunk(body)
            embeddings = await ai_service.embed(chunks)
            for i, (content, emb) in enumerate(zip(chunks, embeddings)):
                db.add(
                    HistoryChunk(
                        document_id=doc.id,
                        chunk_index=i,
                        content=content,
                        embedding=emb,
                    )
                )
            print(f"Ingested {path.name}: {len(chunks)} chunks")

        await db.commit()
    print("Ingestion complete.")


if __name__ == "__main__":
    asyncio.run(main())
