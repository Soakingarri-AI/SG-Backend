# Ask SoakinGarri — RAG corpus

Drop raw and parsed African-history source blocks into
`african_history/`. Each file may carry YAML front-matter (`title`, `era`,
`region`) which becomes searchable metadata.

## Ingestion

The backend ingests these files into `history_documents` + `history_chunks`
(pgvector embeddings) via:

```bash
# from apps/api
python -m scripts.ingest_history   # chunk + embed + upsert
```

At query time `POST /api/v1/ask` embeds the user question and runs an IVFFlat
cosine search over `history_chunks`, returning the answer with source citations.
