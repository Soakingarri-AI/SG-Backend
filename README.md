# SoakinGarri AI

AI products for African learners, creators, and builders. There are six tools,
each on its own `*.soakingarri.com` subdomain, and they all share one backend
and one account.

| | |
|---|---|
| `apps/api` | FastAPI backend: auth plus every product under `/api/v1` ([API.md](apps/api/API.md)) |
| `apps/web` | Next.js frontend; one deployment serves every subdomain |
| `apps/rag_collector` | Service that finds open-access PDFs for the Ask corpus ([README](apps/rag_collector/README.md)) |
| `data/` | Ask history corpus and Factorizer manufacturing reference data |
| `docs/` | Deployment runbooks and planning notes |
| `infra/` | Target AWS topology (Terraform / ECS sketches) |

## Quick start

```bash
cp .env.example .env
docker compose up -d postgres redis api   # http://localhost:8000/docs
docker compose up web                     # http://localhost:3000
```

Product pages run locally on subdomains, e.g. `http://ask.localhost:3000`.

## Docs

- [CLAUDE.md](CLAUDE.md): architecture, conventions, commands, and gotchas
- [docs/deploy/ec2-t3small.md](docs/deploy/ec2-t3small.md): current production runbook
- [docs/deploy/vercel.md](docs/deploy/vercel.md) · [github-pages.md](docs/deploy/github-pages.md) · [ec2.md](docs/deploy/ec2.md)
