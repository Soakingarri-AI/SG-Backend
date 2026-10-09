# Ask SoakinGarri — Phase 1 Backend Implementation Tracker

> **Path mapping note.** The mission spec references `backend/...` and `app/api/v1/...`.
> In this repo the backend lives at **`apps/api/`** and routers live in
> **`app/routers/`**, mounted under `/api/v1` by `app/main.py`. All targets below use
> the real repo paths. Similarly, `tests/backend/` → `apps/api/tests/`, and
> `tests/security/` → `apps/api/tests/security/`.

**Goal:** full Ask backend slice with a **stubbed, pluggable RAG interface** that
returns valid contract responses (`sources: []`) until the vector index (2,000+ PDFs,
currently in the data pipeline) is loaded.

---

## A. Database models — `app/models/chat_session.py`

The shared polymorphic chat store (`chat_sessions` / `chat_messages`) already exists
and is used by every tool; Ask rides on it rather than forking a schema.

- [x] `ChatSession`: UUID pk, `user_id` FK → `users.id`, `title`, `tool_type`
      discriminator, `created_at` / `updated_at` (TimestampMixin).
      *Note:* the enum value is `ToolType.ask` (`"ask"`), the platform's existing
      discriminator for this tool — kept as-is because it is a native PG enum already
      in production; it is internal and never exposed in the API contract.
- [x] `ChatMessage`: UUID pk, `session_id` FK, `role` enum
      (`user`/`assistant`/`system`/`tool`), `content` text, `meta` JSONB
      (mode, sources, model), `created_at`.
- [x] Add `user_id` FK → `users.id` on `ChatMessage` (nullable for pre-existing rows).
- [x] `AskSessionConfig`: satisfied via `ChatSession.meta` JSONB
      (`{"learning_mode": ...}`) — no extra table needed.
- [x] Alembic migration `0002_chat_message_user_id` created and applied.
      **Acceptance:** `alembic upgrade head` succeeds; `chat_messages.user_id` exists.

## B. Pydantic schemas — `app/schemas/ask.py`

- [x] `AskRequest`: `prompt` (str, 1..4000), `session_id` (optional UUID),
      `learning_mode` (`beginner|normal|advanced`, default `normal`).
- [x] `AskSource`: `title`, `source_url` (optional), `snippet`, `category`.
- [x] `AskResponse`: `session_id`, `message_id`, `answer`, `learning_mode`, `sources`.
- [x] `AskSessionListResponse`: `id`, `title`, `created_at`, `updated_at`.
- [x] `AskSessionDetailResponse`: session fields + full ordered message history.
- [x] Old `query`/`mode`/`citations` Ask contract removed from
      `app/schemas/modules.py` (frontend page updated to match — see G).
      **Acceptance:** OpenAPI schema at `/docs` reflects the new contract.

## C. System prompts — `app/prompts/`

- [x] `ask_soakingarri_system.md`: grounded AI tutor, African historical/cultural
      depth + general STEM, with the three learning-mode style blocks
      (beginner / normal / advanced) selected via a `{learning_mode_style}` slot.
- [x] `ask_context_template.md`: wrapper that isolates retrieved RAG text inside
      `<retrieved_sources>` boundaries, labels it untrusted data, and instructs the
      model to never follow instructions found inside it (prompt-injection defense).
- [x] `app/prompts/__init__.py` loader (`load_prompt(name)`) + package-data entry in
      `pyproject.toml` so the `.md` files ship in the wheel/image.
      **Acceptance:** prompts load by name at runtime; injection delimiters occurring
      *inside* source text or user prompts are neutralized before templating.

## D. Core services — `app/services/`

- [x] `rag_service.py`
  - [x] `RetrievedChunk` dataclass (`title`, `source_url`, `snippet`, `category`,
        `content`, `score`).
  - [x] `RAGService.retrieve_context(query, scope="ask_soakingarri", top_k=3)
        -> list[RetrievedChunk]` — **stub returns `[]`** while the index loads.
  - [x] Dev mock payload behind `ENVIRONMENT == "development"` + `RAG_DEV_MOCK=true`.
  - [x] Pluggable real backend: the existing pgvector cosine-search path is preserved
        behind `RAG_ENABLED=true` (default **false**), so flipping one env var
        activates retrieval once the ingest pipeline lands.
  - [x] `format_context(chunks) -> str` with per-source `[Source N]` labels.
- [x] `ai_service.py` — multi-LLM completion wrapper.
      *Note:* already exists as the platform's centralized provider-agnostic service
      (Bedrock primary / Anthropic fallback, tenacity retry, token usage). Kept as-is
      rather than introducing OpenAI/Groq clients (unused deps); its interface
      (`system` + `messages` → `AIResult`) is exactly the pluggable seam the spec asks
      for, and new providers slot into `_build_client()`.
- [x] `ask_service.py` — orchestrator:
      (1) fetch-or-create `ChatSession` (ownership-checked),
      (2) load last N turns of history (`ASK_HISTORY_MESSAGES`, default 12),
      (3) `rag_service.retrieve_context`,
      (4) wrap context + question via `ask_context_template.md`,
      (5) `ai_service.complete`,
      (6) persist user + assistant `ChatMessage` rows (meta: mode, sources, model),
      (7) return `AskResponse`.
- [x] `ask_retrieval_policy.py` — input sanitization (control chars, whitespace,
      delimiter neutralization), token-count validation (`ASK_MAX_PROMPT_TOKENS`),
      retrieval scope allowlist (`ask_soakingarri` only).
      **Acceptance:** oversized/garbage input rejected with 422 before any LLM call;
      unknown scope raises.

## E. API router — `app/routers/ask.py` (mounted at `/api/v1/ask`)

All endpoints: JWT `get_current_user` dependency + shared rate limiter; CORS is
already cross-subdomain via `app/main.py`.

- [x] `POST /api/v1/ask` — submit prompt, create/continue session, return
      `AskResponse` (with `sources: []` while RAG is stubbed).
- [x] `GET /api/v1/ask/sessions` — caller's Ask sessions, `updated_at DESC`.
- [x] `GET /api/v1/ask/sessions/{id}` — full message history; **403** when the
      session belongs to another user, **404** when it doesn't exist.
- [x] `DELETE /api/v1/ask/sessions/{id}` — hard-delete session + messages
      (FK `ondelete=CASCADE`), 204; same 403/404 ownership semantics.
- [x] Router registered in `app/main.py` under `settings.API_V1_PREFIX` (pre-wired).
      **Acceptance:** all four routes visible in OpenAPI; 401 without a token.

## F. Tests

- [x] `tests/test_ask_api.py`
  - [x] Session creation + both conversation turns persisted (user + assistant).
  - [x] Multi-turn: second POST with `session_id` appends to the same session and
        history is passed to the model.
  - [x] Retrieval fallback: stubbed RAG → `sources: []`, valid contract.
  - [x] Auth: 401 with no token; 403 reading/deleting another user's session;
        404 for unknown session ids.
  - [x] Mode switching: beginner/normal/advanced select different system-prompt
        style blocks; `learning_mode` echoed and persisted in session meta.
  - [x] Session list ordering + detail contents; delete removes messages.
- [x] `tests/security/test_ask_prompt_injection.py`
  - [x] Malicious delimiters in the **user prompt** (fake `<retrieved_sources>`,
        "ignore previous instructions", fake system tags) are neutralized and cannot
        terminate/forge the context block.
  - [x] Malicious **RAG chunk content** (injected via mock) cannot escape the
        `<retrieved_sources>` wrapper or introduce forged instructions.
  - [x] System prompt integrity: mode style + grounding rules always present and
        always ordered before untrusted content.
- [x] Full suite green (run inside compose: postgres+pgvector, Redis db 1,
      `ai_service.complete` monkeypatched — no live LLM calls).
      **Acceptance:** `pytest` exit 0.

## G. Frontend contract sync — `apps/web/src/app/ask/page.tsx`

- [x] Update the Ask page to the new contract (`prompt`/`learning_mode`/`sources`,
      `AskSource` fields) so the deployed UI keeps working; empty `sources` renders a
      "source library loading" note instead of an empty panel.

---

## Deferred / follow-ups (out of Phase 1 scope)

- [ ] Load the real vector index; flip `RAG_ENABLED=true`; extend
      `tests` with retrieval-quality checks against seeded chunks.
- [ ] Streaming responses (SSE) for the Ask UI.
- [ ] Optional soft-delete (`deleted_at`) if session recovery becomes a product need.
- [ ] Additional `ai_service` providers (OpenAI / Groq) if cost/latency routing lands.
