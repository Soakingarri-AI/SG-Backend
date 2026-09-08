"""FastAPI application entrypoint.

Assembles the unified backend that serves every ``*.soakingarri.com`` subdomain.
All routers mount under ``/api/v1``. CORS is locked to the configured origins;
locally it also permits ``*.localhost`` for subdomain dev.
"""
from __future__ import annotations

import logging
import re
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.redis import close_redis, get_redis
from app.routers import (
    afro,
    ask,
    auth,
    examflow,
    factorizer,
    infiniteparts,
    memes,
)

def _configure_logging() -> None:
    """Send this application's own log records to stdout.

    Uvicorn configures only its own loggers, so without this every record from
    ``app.*`` is discarded — including the ones reporting a failed transactional
    email, which is deliberately non-fatal and would otherwise fail silently.

    A handler is attached to the ``app`` namespace rather than calling
    ``logging.basicConfig``, which is a no-op once the root logger already has
    handlers and would leave us just as blind.
    """
    app_logger = logging.getLogger("app")
    if app_logger.handlers:  # already configured (e.g. reload)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    app_logger.addHandler(handler)
    app_logger.setLevel(settings.LOG_LEVEL)
    app_logger.propagate = False


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the Redis pool.
    get_redis()
    yield
    await close_redis()


DESCRIPTION = """
Unified backend for every **`*.soakingarri.com`** product surface. A single
account and session work across all subdomains; every module is served from this
one API under the `/api/v1` prefix.

### Modules
| Tag | Subdomain | What it does |
|-----|-----------|--------------|
| **auth** | (all) | Registration, login, token refresh/rotation, logout, password reset |
| **ask** | ask. | RAG African-history teaching assistant with inline citations |
| **examflow** | examflow. | Practice-exam generation, submission, scoring, AI tutoring |
| **afrosimulator** | afrosimulator. | Cultural multi-agent simulations (async) |
| **memes** | memes. | Expectation-vs-reality meme generation |
| **infiniteparts** | infiniteparts. | Natural language → validated parametric 3D part |
| **factorizer** | factorizer. | Industrial factory-setup plan generator |

### Authentication
Obtain a token pair from `POST /api/v1/auth/login`, then send it either as a
`Authorization: Bearer <access_token>` header **or** rely on the
`access_token` cookie (scoped to `.soakingarri.com`, so one login is honoured on
every subdomain). Access tokens are short-lived; rotate them with
`POST /api/v1/auth/refresh`. Tokens can be revoked (logout / password reset).

### Rate limiting
Endpoints are rate limited per authenticated user (or client IP when anonymous)
using a fixed 60-second window. Exceeding it returns `429` with a `Retry-After`
header.
"""

TAGS_METADATA = [
    {"name": "system", "description": "Health checks and service metadata."},
    {"name": "auth", "description": "Accounts, sessions, token lifecycle, and password reset."},
    {"name": "ask", "description": "Ask SoakinGarri — cited, RAG-grounded African-history answers."},
    {"name": "examflow", "description": "Generate, take, and get AI-graded practice exams."},
    {"name": "afrosimulator", "description": "Asynchronous, culturally-safe multi-agent dialogues."},
    {"name": "memes", "description": "Structured expectation-vs-reality meme generation."},
    {"name": "infiniteparts", "description": "Parametric 3D part specifications from a prompt."},
    {"name": "factorizer", "description": "End-to-end industrial factory-setup plans."},
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    summary="Unified AI backend for all *.soakingarri.com subdomains.",
    description=DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    contact={"name": "SoakinGarri Engineering", "email": "engineering@soakingarri.com"},
    license_info={"name": "Proprietary"},
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# In dev, allow any localhost subdomain; in prod, use the strict allowlist.
_cors_kwargs: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.is_production:
    _cors_kwargs["allow_origins"] = settings.CORS_ORIGINS
else:
    _cors_kwargs["allow_origin_regex"] = re.compile(
        r"^https?://([a-z0-9-]+\.)?localhost(:\d+)?$"
    ).pattern

app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "service": "soakingarri-api", "env": settings.ENVIRONMENT}


for r in (auth, ask, examflow, afro, memes, infiniteparts, factorizer):
    app.include_router(r.router, prefix=settings.API_V1_PREFIX)
