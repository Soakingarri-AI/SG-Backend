# Infrastructure — AWS mapping

This directory documents how the dev `docker-compose` topology maps to production
AWS managed services. IaC is provided as Terraform sketches under `terraform/`
and ECS task definitions under `ecs/`.

| Dev container      | AWS production service                         |
| ------------------ | ---------------------------------------------- |
| `web` (Next.js)    | ECS Fargate service behind ALB + CloudFront    |
| `api` (FastAPI)    | ECS Fargate service behind ALB                 |
| `postgres`         | Amazon Aurora PostgreSQL (pgvector) + RDS Proxy |
| `redis`            | Amazon ElastiCache for Redis                    |
| S3 asset service   | Amazon S3 + CloudFront (`cdn.soakingarri.com`)  |
| AI inference       | Amazon Bedrock (Claude + Titan embeddings)      |

## DNS / subdomain routing

A single CloudFront distribution + Route 53 wildcard record
(`*.soakingarri.com`) fronts the `web` service. Next.js `middleware.ts` inspects
the `Host` header and rewrites each subdomain onto its App Router segment, so one
deployment serves all six products and the apex marketing site.

Secrets (JWT key, DB creds, API keys) come from AWS Secrets Manager / SSM
Parameter Store and are injected as ECS task definition secrets — never baked
into images.
