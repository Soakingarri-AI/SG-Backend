"""FastAPI application entrypoint.

Assembles the unified backend that serves every ``*.soakingarri.com`` subdomain.
All routers mount under ``/api/v1``. CORS is locked to the configured origins;
locally it also permits ``*.localhost`` for subdomain dev.
"""
from __future__ import annotations

import re
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the Redis pool.
    get_redis()
    yield
    await close_redis()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    docs_url="/docs",
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
