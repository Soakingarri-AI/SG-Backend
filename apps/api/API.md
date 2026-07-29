# SoakinGarri AI — API Reference

Unified FastAPI backend serving every `*.soakingarri.com` subdomain. A single
account and session work across all of them; every module mounts under the
`/api/v1` prefix on one service.

- **Interactive docs (Swagger UI):** `GET /docs`
- **Alternative docs (ReDoc):** `GET /redoc`
- **OpenAPI schema:** `GET /openapi.json`
- **Health check:** `GET /health` (unprefixed)

---

## Table of contents

- [Base URLs](#base-urls)
- [Authentication](#authentication)
- [Errors](#errors)
- [Rate limiting](#rate-limiting)
- [Endpoints](#endpoints)
  - [System](#system)
  - [Auth](#auth)
  - [Ask SoakinGarri](#ask-soakingarri)
  - [ExamFlow](#examflow)
  - [AfroSimulator](#afrosimulator)
  - [Meme Generator](#meme-generator)
  - [InfiniteParts](#infiniteparts)
  - [Factorizer](#factorizer)
- [Running locally](#running-locally)
- [Testing](#testing)

---

## Base URLs

| Environment | Base URL |
|-------------|----------|
| Local (docker-compose) | `http://localhost:8000` |
| Production | `https://api.soakingarri.com` |

All application endpoints below are relative to `<base>/api/v1`. For example
`POST /auth/login` is `http://localhost:8000/api/v1/auth/login` locally.

---

## Authentication

Auth is JWT-based (HS256) with separate **access** and **refresh** tokens.

1. `POST /auth/register` — create an account.
2. `POST /auth/login` — receive `{ access_token, refresh_token }` **and** get the
   tokens set as cookies scoped to `.soakingarri.com` (so one login works on every
   subdomain).
3. Send the access token on protected requests, either way:
   - **Header:** `Authorization: Bearer <access_token>`, or
   - **Cookie:** `access_token` (sent automatically by the browser).
4. `POST /auth/refresh` — when the access token expires, exchange the refresh token
   for a new pair. **Refresh tokens are single-use** — the old one is denylisted on
   rotation, so replaying it returns `401`.
5. `POST /auth/logout` — clears cookies and revokes the presented tokens.

Token lifetimes (defaults, configurable): access **30 min**, refresh **14 days**.

### Revocation model

Even though JWTs are stateless, they are revocable via Redis:

- **Per-token denylist** (`jti`): set on logout and on refresh rotation.
- **Per-user session epoch:** a password reset stamps a timestamp; every token
  issued before it is rejected, killing all outstanding sessions at once.

---

## Errors

Standard HTTP status codes. Error bodies use FastAPI's shape:

```json
{ "detail": "Human-readable message" }
```

Validation errors (`422`) return the detailed field-level list produced by
Pydantic/FastAPI.

| Status | Meaning |
|--------|---------|
| `400` | Bad request (e.g. invalid/expired reset token) |
| `401` | Missing, invalid, expired, or revoked credentials |
| `404` | Resource not found or not owned by the caller |
| `409` | Conflict (e.g. email already registered, session already submitted) |
| `422` | Request body failed validation |
| `429` | Rate limit exceeded — see `Retry-After` header |

---

## Rate limiting

A fixed **60-second window** counter, keyed by authenticated user id (or client IP
when anonymous) **and** endpoint path, backed by Redis. The user id is taken from
the access token (header or cookie) with a local signature check; anonymous
requests are keyed by the proxy-supplied `X-Forwarded-For` address when present,
else the socket peer. Defaults: 60 requests/min; `login` is capped at 10/min and
`password-reset/request` at 5/min. On breach: `429 Too Many Requests` with a
`Retry-After` (seconds) header.

---

## Endpoints

> 🔒 = requires authentication.

### System

#### `GET /health`
Liveness/metadata probe. Unauthenticated, unprefixed.

```json
{ "status": "ok", "service": "soakingarri-api", "env": "development" }
```

---

### Auth

#### `POST /auth/register` → `201`
```json
// request
{ "email": "user@example.com", "password": "supersecret1", "full_name": "Ada" }
// response (UserRead)
{ "id": "uuid", "email": "user@example.com", "full_name": "Ada",
  "is_active": true, "created_at": "2026-07-21T09:00:00Z" }
```
`password` must be 8–128 chars. Returns `409` if the email already exists.

#### `POST /auth/login` → `200`
```json
// request
{ "email": "user@example.com", "password": "supersecret1" }
// response (TokenPair) + Set-Cookie: access_token, refresh_token
{ "access_token": "jwt...", "refresh_token": "jwt...", "token_type": "bearer" }
```
`401` on bad credentials. Rate limited to 10/min.

#### `POST /auth/refresh` → `200`
```json
{ "refresh_token": "jwt..." }
```
The body is **optional**: browser clients can send no body and the httponly
`refresh_token` cookie is used instead (the body takes precedence when both are
present). Returns a fresh `TokenPair`. Rotation is atomic — the submitted
refresh token is single-use, and of any concurrent requests presenting the same
token exactly one succeeds. `401` if the token is missing, invalid, already
rotated, or from an invalidated session.

#### `POST /auth/logout` → `204` 🔒
Optional body `{ "refresh_token": "jwt..." }` for header-based clients. Revokes the
access token (from header or cookie) and the refresh token (from cookie or body),
and clears cookies.

#### `GET /auth/me` → `200` 🔒
Returns the current `UserRead`.

#### `POST /auth/password-reset/request` → `200`
```json
// request
{ "email": "user@example.com" }
// response (always identical, to prevent account enumeration)
{ "message": "If that email is registered, a reset link has been sent." }
```
The token is delivered by email. **Only in development** does the response also
include `"reset_token": "..."` to enable testing (staging behaves like
production). Rate limited to 5/min. Token TTL: 30 min.

#### `POST /auth/password-reset/confirm` → `200`
```json
{ "token": "<reset_token>", "new_password": "newsecret1" }
```
Updates the password, consumes the token (single-use), and **invalidates all
existing sessions**. `400` if the token is invalid or expired.

---

### Ask SoakinGarri

RAG teaching assistant over African-history sources with inline citations.

#### `POST /ask` → `200` 🔒
```json
// request (AskRequest)
{ "query": "Who was Queen Amina of Zazzau?",
  "mode": "normal",                 // "beginner" | "normal" | "advanced"
  "session_id": null }              // optional; continues an existing chat
// response (AskResponse)
{ "answer": "…grounded answer citing [Source 1]…",
  "citations": [
    { "document_title": "Queen Amina", "source_path": "…/queen_amina.md",
      "snippet": "…", "score": 0.83 }
  ],
  "session_id": "uuid",
  "mode": "normal" }
```
`query` is 1–2000 chars. Messages are persisted to the shared chat store.

---

### ExamFlow

Practice exams: generate → submit → score → AI tutoring.

#### `POST /examflow/sessions` → `201` 🔒
```json
// request (ExamGenerateRequest)
{ "board": "JAMB",                  // WAEC | JAMB | NECO | COMMON_ENTRANCE
  "subject": "Mathematics",
  "num_questions": 20,              // 5–100
  "years": [2021, 2022],           // optional filter
  "duration_seconds": 3600 }       // 300–14400
// response
{ "session_id": "uuid", "duration_seconds": 3600,
  "questions": [
    { "position": 0, "id": "uuid", "stem": "If 2x + 3 = 11, what is x?",
      "options": [ { "key": "A", "text": "2" }, { "key": "B", "text": "4" } ] }
  ] }
```
`404` if no questions match the board/subject filter. Correct answers are **not**
included in the response.

#### `POST /examflow/sessions/{session_id}/submit` → `200` 🔒
```json
// request (ExamAnswerSubmit) — maps session_question_id → chosen option key
{ "answers": { "uuid-of-session-question": "B" } }
// response
{ "score": 85, "correct": 17, "total": 20,
  "corrections": [
    { "position": 3, "stem": "…", "your_answer": "A",
      "correct_answer": "C", "explanation": "…" }
  ] }
```
`404` if the session isn't yours; `409` if already submitted.

#### `GET /examflow/sessions/{session_id}/tutor/{position}` → `200` 🔒
Step-by-step AI explanation for one question in a completed session.
```json
{ "position": 3, "explanation": "Step 1 … therefore C is correct because …" }
```

---

### AfroSimulator

Asynchronous, culturally-safe multi-agent dialogues.

#### `POST /afro/simulations` → `202` 🔒
```json
// request (SimulationCreate)
{ "topic": "Resolving a land dispute between two families",
  "cultures": ["yoruba", "igbo"],  // ≥2 of: yoruba | igbo | hausa
  "max_turns": 8 }                 // 2–20
// response
{ "simulation_id": "uuid", "status": "pending" }
```
The dialogue runs in the background. Poll the endpoint below for progress.

#### `GET /afro/simulations/{simulation_id}` → `200` 🔒
```json
{ "id": "uuid",
  "status": "running",             // pending | running | completed | failed
  "topic": "…",
  "summary": null,                 // populated when completed
  "turns": [
    { "turn_index": 0, "culture": "yoruba", "speaker": "Adéyẹmí",
      "content": "…", "proverbs": ["…"] }
  ] }
```
Each generated turn passes a cultural-safety filter before being stored.

---

### Meme Generator

#### `POST /memes` → `200` 🔒
```json
// request (MemeRequest)
{ "prompt": "Studying the night before an exam", "style": "nigerian_student" }
// response
{ "id": "uuid",
  "expectation": "I'll read the whole textbook tonight",
  "reality": "Watched three hours of YouTube, read one page",
  "caption": "The syllabus was undefeated 😭",
  "tokens": 142 }
```
`reality` is generated as the polar opposite of `expectation`. `prompt` ≤ 500 chars.

---

### InfiniteParts

Natural-language design prompt → validated parametric 3D part envelope.

#### `POST /infiniteparts` → `200` 🔒
```json
// request (PartRequest)
{ "prompt": "A 100mm mounting bracket with two bolt holes" }
// response
{ "id": "uuid",
  "parameters": {
    "name": "Mounting Bracket", "units": "mm",
    "length": 100, "width": 40, "height": 20,
    "hole_diameter": 6, "wall_thickness": 2.0, "tolerance": 0.1,
    "features": ["fillet", "counterbore"] },
  "notice": "⚠️ Generated dimensions are AI estimates … verify tolerances …",
  "tokens": 210 }
```
The `notice` (manufacturing-validation warning) is always returned and must be
surfaced to the user.

---

### Factorizer

#### `POST /factorizer/plans` → `200` 🔒
```json
// request (FactoryWizardInput)
{ "target_product": "Bottled water",
  "category": "beverages",
  "budget_usd": 250000,
  "automation_level": "semi_automated",   // manual | semi_automated | fully_automated
  "region": "NG",
  "raw_materials": ["PET resin", "caps"] }
// response
{ "id": "uuid",
  "plan_markdown": "# Executive Summary\n…15 sections…",
  "machines": [
    { "name": "Blow moulder", "purpose": "Form PET bottles",
      "estimated_cost_usd": 45000, "quantity": 1 } ],
  "process_flow": ["Water treatment", "Bottle forming", "Filling", "Capping"],
  "cost_breakdown": [ { "category": "Machinery", "amount_usd": 120000 } ] }
```
Returns a 15-section markdown plan plus structured data for the interactive UI,
grounded in the local `data/manufacturing` reference KB.

---

## Running locally

From the repo root:

```bash
docker compose up -d postgres redis      # dependencies
docker compose up api                     # runs migrations + uvicorn on :8000
# or the full stack (adds the Next.js web app on :3000):
docker compose up
```

Then open `http://localhost:8000/docs`.

Seed some dev data (personas + a sample exam question):

```bash
docker compose run --rm api python -m scripts.seed
```

Configuration is read from environment / `.env` (see `.env.example`). Key vars:
`DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `CORS_ORIGINS`, `AI_PROVIDER`
(`bedrock` | `anthropic`), `ANTHROPIC_API_KEY`.

---

## Testing

The suite runs against the docker-compose Postgres + Redis and isolates itself
(throwaway `authtest-*@example.com` users; Redis DB index 1):

```bash
docker compose up -d postgres redis
docker compose run --rm -e REDIS_URL=redis://redis:6379/1 api pytest -q
```
