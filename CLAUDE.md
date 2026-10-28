# CLAUDE.md

Guidance for working in this repository.

## What this is

**SoakinGarri AI**: six AI products for African learners, each served on its own
`*.soakingarri.com` subdomain. They share **one FastAPI backend, one account and
one Next.js frontend**, plus a separate data-collection service that feeds the
Ask RAG corpus.

| Product | Subdomain | Backend router | What it does |
|---|---|---|---|
| Ask SoakinGarri | `ask.` | `routers/ask.py` → `services/ask_service.py` | African-history tutor with learning modes, sessions and citations |
| ExamFlow | `examflow.` | `routers/examflow.py` | WAEC/JAMB/NECO practice tests, scoring, AI tutoring |
| AfroSimulator | `afrosimulator.` | `routers/afro.py` | Yoruba/Igbo/Hausa persona dialogues (background task) |
| Memes | `memes.` | `routers/memes.py` | Expectation-vs-reality memes via strict JSON output |
| InfiniteParts | `infiniteparts.` | `routers/infiniteparts.py` | Prompt → validated parametric 3D part |
| Factorizer | `factorizer.` | `routers/factorizer.py` | 15-section factory-setup plan |

## Layout

```
apps/
  api/            FastAPI backend (Python 3.11, async SQLAlchemy, Alembic)
    app/
      core/       config, database, redis, security (JWT), token_service
                  (revocation), verification_tokens, rate_limit
      models/     SQLAlchemy models; import every new model in models/__init__.py
      schemas/    Pydantic request/response contracts
      routers/    thin HTTP layer, one per product + auth
      services/   business logic; ai_service.py is the only LLM entry point
                  (rag_service = retrieval, storage_service = blob backend,
                  file_extraction = upload text)
      prompts/    .md prompt templates loaded with load_prompt(name)
      templates/email/   Resend HTML templates (string.Template)
    alembic/versions/    migrations (0001 = metadata.create_all baseline)
    scripts/      seed.py, ingest_history.py, init_pgvector.sql
    tests/        pytest (needs compose Postgres + Redis db 1)
    API.md        public API reference: keep in sync with contract changes
  web/            Next.js 14 App Router + Tailwind
    middleware.ts subdomain → route-segment rewrite (ask. → /ask)
    src/app/(marketing)/   apex site + legal pages
    src/app/<product>/     one page per product
    src/lib/      api.ts (fetch wrapper, credentials: include), config.ts
  rag_collector/  standalone PDF-discovery service → Google Sheet (flat scripts):
                  dork_sheet_collector.py (main loop + web-search dorking),
                  source_harvesters.py (repository APIs + OAI-PMH harvesting),
                  author_resolver.py (column F authors), backfill_authors.py,
                  author_origin.py (column G author_is_african), backfill_origin.py,
                  llm_client.py (DeepSeek on Bedrock; OpenAI fallback),
                  pdf_downloader.py (G=TRUE rows → S3; sets H/I) +
                  install_downloader.sh (hourly systemd timer)
data/
  ask_soakingarri/african_history/   markdown corpus for ingest_history.py
  manufacturing/                     Factorizer reference JSON
docs/
  deploy/         ec2-t3small.md (current prod runbook), ec2.md, vercel.md,
                  github-pages.md
  ask-phase1-tasks.md               Ask backend tracker + deferred follow-ups
infra/            bedrock_kb/setup_kb.py builds the live Ask Knowledge Base;
                  terraform/ + ecs/ are sketches of the target topology (not live)
assets/brand/     source brand artwork (not served)
.local/           GITIGNORED: SSH keys, VPN profiles, personal notes/exports
```

## Commands

Everything runs through Docker Compose from the repo root.

```bash
docker compose up -d postgres redis api        # API on :8000, docs at /docs
docker compose up web                          # web on :3000

# API tests: run INSIDE the compose network (see Gotchas); Redis db 1 isolates
# the suite's flushdb from dev data in db 0.
docker compose run --build --rm -e REDIS_URL=redis://redis:6379/1 api python -m pytest tests -q

# Migrations (also run automatically when the api container starts)
docker compose exec api alembic revision --autogenerate -m "describe change"
docker compose exec api alembic upgrade head

docker compose exec api python -m scripts.seed            # personas + sample questions
docker compose exec api python -m scripts.ingest_history  # embed data/ask_soakingarri

# Lint / type-check
docker compose exec api ruff check app tests
cd apps/web && npm run lint && npm run typecheck
```

AWS (SoakinGarri account `431856071890`): always pass
`--profile soakingarri --region us-east-1`. The other local profiles belong to
unrelated accounts. The local API container reads the same profile:
`docker-compose.override.yml` mounts `~/.aws`, and `.env` sets
`AWS_PROFILE=soakingarri`. Prod ignores the override because it always runs
with explicit `-f` files.

RAG collector (its own venv, not Docker):

```bash
cd apps/rag_collector
.venv/Scripts/python.exe dork_sheet_collector.py --dry-run --once --max-queries 2
.venv/Scripts/python.exe -m pip install -r requirements.txt   # use -m pip; pip.exe launcher fails
```

## Architecture conventions

- **Routers stay thin.** Auth, rate limiting and ownership checks go in the
  router; the rules go in `services/`. `auth.py` → `auth_service.py` and
  `ask.py` → `ask_service.py` are the reference pattern.
- **All LLM calls go through `services/ai_service.py`** (`complete`,
  `complete_json` for schema-validated JSON, `embed`). The provider is picked by
  `AI_PROVIDER` (`bedrock` | `anthropic` | `openai`). Never import a provider
  SDK anywhere else.
  - **Bedrock uses the Converse API**, so any Bedrock model works by changing
    `AI_MODEL`. The default is **DeepSeek V3.2** (`deepseek.v3.2`); R1 is
    `us.deepseek.r1-v1:0`. **Prod runs DeepSeek V3.2 on Bedrock**, through the
    instance role `soakingarri-api`. Every model call stays on AWS; the
    OpenAI provider is still in the code but unused, and must not come back.
  - DeepSeek is **text-only**. Check `ai_service.supports_images` before
    sending images or telling the model a picture is attached.
  - **Embeddings are pinned by `EMBEDDING_PROVIDER`**, separately from chat
    (Titan v2, 1024 dims, the `history_chunks` column width). Never change the
    embedding model without re-embedding the whole index.
- **Shared chat store:** `chat_sessions` / `chat_messages` are shared by every
  tool, told apart by `tool_type`. Tool-specific data goes in the `meta` JSONB,
  not in new tables.
- **Every endpoint gets rate limiting:** add `dependencies=[Depends(rate_limit)]`
  on the router, or `rate_limited(n)` for a tighter per-endpoint limit.
- **Ownership:** return 404 for missing resources. Ask returns 403 for sessions
  owned by someone else; the other tools return 404.
- **Untrusted text in prompts:** sanitise it with
  `ask_retrieval_policy.sanitize_text` and wrap retrieved content in the
  `<retrieved_sources>` template, uploaded files in `<user_attachments>`.
  Any new fence tag must be added to `_RESERVED_TAG_RE`, or content can close
  its own fence. `tests/security/` enforces this.
- **Chat uploads:** files go to `POST /uploads` first and are referenced by id
  on `POST /ask`. Types are sniffed from magic bytes, never from the declared
  `Content-Type`. Blobs go through `storage_service` (`STORAGE_BACKEND`:
  `local` volume by default, `s3` once AWS credentials exist).
- **Settings** live only in `core/config.py` (pydantic-settings, cached at
  import). Add new variables there **and** to `.env.example`.

### Auth model (keep these properties when changing auth)

- JWTs carry `type` (access/refresh) and `jti`. Redis handles revocation in two
  ways: a per-`jti` denylist, and a per-user "session epoch" that invalidates
  every token when the password changes.
- Refresh tokens are single-use: `consume_token` does an atomic `SET NX`.
- Cookies are scoped to `.soakingarri.com` in production so one login works on
  every subdomain. Bearer headers work too.
- Email verification and password-reset tokens are opaque, stored as SHA-256
  hashes, and redeemed with `GETDEL`. Issuing a new one revokes the previous one.
- Never reveal whether an email is registered. Reset and resend always return
  the same message, and login spends the same time on unknown emails.
- Email sending runs as a `BackgroundTask` and never fails a request. With no
  `RESEND_API_KEY`, emails are logged instead of sent.

## Current state / known gaps

- **Ask retrieval uses a Bedrock Knowledge Base** (`soakingarri-ask-kb`, id
  `R2EDMWMFLK`, see `infra/README.md`). The pipeline: `apps/rag_collector`
  lists PDFs in a Google Sheet; `pdf_downloader.py` copies the African-author
  ones into `s3://soakingarri-rag-corpus/pdfs/` with `.metadata.json`
  sidecars; then the Knowledge Base chunks them, embeds them with Titan v2
  and stores them in S3 Vectors. The API reads it with `RAG_ENABLED=true`,
  `RAG_BACKEND=bedrock_kb` and `BEDROCK_KB_ID`, and **prod runs it**. Hits
  scoring below `RAG_MIN_SCORE` (0.70) are dropped, so off-topic questions get
  no citations; a failed search answers without sources.
  `RAG_BACKEND=pgvector` is the older path over `history_chunks`
  (`scripts.ingest_history`). `RAG_DEV_MOCK=true` (development only) returns
  one canned chunk.
- `apps/web` has **no login, register, verify-email or reset-password pages**.
  Email links target `FRONTEND_URL` (the Vercel frontend).
- `complete_json` retries on top of `complete`'s own retries (up to 16 calls
  in the worst case). Provider SDK calls run in worker threads
  (`asyncio.to_thread`), so a slow model no longer blocks the event loop.
- **The prod `api` server's role `soakingarri-api`** allows DeepSeek and Titan
  invocation and `Retrieve` on the Knowledge Base, nothing else. Its metadata
  hop limit is 2, so containers get the role's credentials. Uploads still use
  the local volume; `STORAGE_BACKEND=s3` would need S3 access added to the role.
- AfroSimulator expects personas for every requested culture; run
  `scripts.seed` first or the simulation fails.
- Legal pages use placeholder company details (`src/lib/company.ts` TODOs).
- The only CI is `.github/workflows/pages.yml`, a static demo build. No tests
  run in CI.

## Gotchas

- **Port 5432 on the dev machine** is taken by a native Windows PostgreSQL, so
  host-run pytest reaches the wrong database. Run tests inside compose (above).
- **`CORS_ORIGINS` is comma-separated**, not JSON. In non-production, any
  `*.localhost` origin is allowed by regex.
- Staging and production **refuse to boot** with the placeholder
  `JWT_SECRET_KEY`.
- Local subdomain dev: `http://ask.localhost:3000`, etc.
- `EMBEDDING_DIM` (1024) is fixed in the `history_chunks` vector column. If it
  changes, you need a migration and a full re-embed.
- **Bedrock model ids:** some models are only callable through an
  inference-profile id (`us.deepseek.r1-v1:0`, `us.anthropic.claude-…`).
  A bare name like `claude-opus-4-8` fails. List them with
  `aws bedrock list-inference-profiles`.
- **DeepSeek R1 spends tokens on hidden reasoning** (about 350 tokens for a
  one-word answer). Give it a generous `max_tokens`, or the visible answer
  comes back empty.
- **Never build `apps/web` on the prod t3.small**: 2 GB RAM, it runs out of
  memory. The frontend is hosted elsewhere.

## Deployment

- **API (prod):** a single EC2 t3.small running Docker Compose (api + postgres +
  redis) behind Nginx/TLS at `api.soakingarri.com`. The box uses an untracked
  `docker-compose.prod.yml` override. Runbook: `docs/deploy/ec2-t3small.md`.
- **Web:** Vercel. The GitHub Pages static demo deploys on every push to `main`.
- **RAG collector:** its own EC2 t3.micro, systemd unit `soakingarri-scraper`,
  plus the hourly `soakingarri-pdf-downloader` timer, which ends by starting
  a Knowledge Base sync when it uploaded PDFs. Its IAM instance role,
  `soakingarri-rag-collector`, grants DeepSeek on Bedrock, read/write on
  the corpus bucket and ingestion on the Knowledge Base, so the LLM steps run
  on DeepSeek there. `~/rag_collector` is a **flat scp'd copy, not a git
  checkout**. Keep the modules flat, and when you add a module, add it to the
  scp list in `apps/rag_collector/README.md`.
- **IAM:** project roles are `soakingarri-*` and carry the permissions
  boundary `soakingarri-workload-boundary`; the `claude-code` user can only
  create or edit roles that have it. A new permission a role needs must fit
  inside the boundary.

## Secrets: never commit

`.env`, `*.pem`, `*.ovpn`, `service_account.json` and all of `.local/` are
gitignored. The SSH key is `.local/keys/sg-key.pem`. The collector's Google
credential lives beside it at `apps/rag_collector/service_account.json`.
**Never `scp -r` a folder that may contain keys; copy files individually.**
Check `git status` for credentials before every commit.

## Git

Work on `main` is committed directly; commits are small with imperative
subjects (e.g. "Attach the log handler to the app namespace…"). Commit only
when asked.
