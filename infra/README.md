# Infrastructure — AWS mapping

This directory documents how the dev `docker-compose` topology maps to production
AWS managed services. IaC is provided as Terraform sketches under `terraform/`
and ECS task definitions under `ecs/`. Those two are sketches, not live.

## Live: the Ask Knowledge Base (`bedrock_kb/`)

`bedrock_kb/setup_kb.py` creates, or finds, the managed RAG index behind Ask:

| Resource | Name |
| --- | --- |
| Corpus (input) | `s3://soakingarri-rag-corpus/pdfs/`, PDFs + `.metadata.json` sidecars |
| Knowledge Base | `soakingarri-ask-kb` (Titan Text Embeddings v2, 1024 dims) |
| Data source | `corpus-pdfs`: fixed 500-token chunks, 15% overlap |
| Vector store | S3 Vectors bucket `soakingarri-rag-vectors`, index `ask-corpus` (cosine) |
| Service role | `soakingarri-kb`, under the `soakingarri-workload-boundary` |

```bash
cd apps/rag_collector   # its venv has a boto3 that knows S3 Vectors
.venv/Scripts/python.exe ../../infra/bedrock_kb/setup_kb.py          # print the ids
.venv/Scripts/python.exe ../../infra/bedrock_kb/setup_kb.py --sync   # ingest and wait
```

The collector box syncs it after every hourly download, and the API searches
it with `RAG_BACKEND=bedrock_kb`. Bedrock skips PDFs over 50 MB.

| Dev container      | AWS production service                         |
| ------------------ | ---------------------------------------------- |
| `web` (Next.js)    | ECS Fargate service behind ALB + CloudFront    |
| `api` (FastAPI)    | ECS Fargate service behind ALB                 |
| `postgres`         | Amazon Aurora PostgreSQL (pgvector) + RDS Proxy |
| `redis`            | Amazon ElastiCache for Redis                    |
| S3 asset service   | Amazon S3 + CloudFront (`cdn.soakingarri.com`)  |
| AI inference       | Amazon Bedrock (DeepSeek V3.2 + Titan embeddings) |
| Ask retrieval      | Bedrock Knowledge Base on S3 Vectors (live, see below) |

## DNS / subdomain routing

A single CloudFront distribution + Route 53 wildcard record
(`*.soakingarri.com`) fronts the `web` service. Next.js `middleware.ts` inspects
the `Host` header and rewrites each subdomain onto its App Router segment, so one
deployment serves all six products and the apex marketing site.

Secrets (JWT key, DB creds, API keys) come from AWS Secrets Manager / SSM
Parameter Store and are injected as ECS task definition secrets — never baked
into images.
