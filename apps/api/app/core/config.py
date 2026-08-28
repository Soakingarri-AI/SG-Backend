"""Centralized, typed application settings.

Loaded once at import and injected everywhere via ``get_settings()``. In AWS,
values arrive from ECS task definition secrets / SSM Parameter Store rather
than a committed ``.env`` file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core ---
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    PROJECT_NAME: str = "SoakinGarri AI"
    ROOT_DOMAIN: str = "soakingarri.com"
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://soakingarri:soakingarri_dev@localhost:5432/soakingarri"
    )

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Auth ---
    JWT_SECRET_KEY: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    COOKIE_DOMAIN: str = ".soakingarri.com"

    # --- CORS ---
    # ``NoDecode`` stops pydantic-settings from JSON-decoding the env value so the
    # validator below can accept a plain comma-separated string (e.g. from .env).
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Rate limiting ---
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 20

    # --- AI ---
    AI_PROVIDER: Literal["bedrock", "anthropic"] = "bedrock"
    AI_MODEL: str = "claude-opus-4-8"
    AI_EMBEDDING_MODEL: str = "amazon.titan-embed-text-v2:0"
    AI_MAX_RETRIES: int = 4
    AI_TIMEOUT_SECONDS: int = 60
    ANTHROPIC_API_KEY: str | None = None
    EMBEDDING_DIM: int = 1024  # Titan v2 default; keep in sync with models

    # --- Ask SoakinGarri / RAG ---
    # The vector index is still being built from the PDF corpus. Until it is
    # loaded, retrieval is stubbed: RAG_ENABLED=false short-circuits to no
    # sources while keeping the pgvector code path one env flip away.
    RAG_ENABLED: bool = False
    # In development only: return a canned mock chunk so the full context
    # pipeline (templating, citations, UI panels) can be exercised end to end.
    RAG_DEV_MOCK: bool = False
    RAG_TOP_K: int = 3
    ASK_HISTORY_MESSAGES: int = 12  # last N messages replayed as conversation memory
    ASK_MAX_PROMPT_TOKENS: int = 1200  # ~4 chars/token heuristic over the 4000-char cap

    # --- AWS ---
    AWS_REGION: str = "us-east-1"
    BEDROCK_REGION: str = "us-east-1"
    S3_BUCKET: str = "soakingarri-assets"
    S3_PUBLIC_BASE_URL: str = "https://cdn.soakingarri.com"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _forbid_default_secret_outside_dev(self) -> "Settings":
        """Refuse to boot staging/production with the placeholder JWT secret —
        a known signing key lets anyone forge tokens for any user."""
        if self.ENVIRONMENT != "development" and self.JWT_SECRET_KEY == "change-me":
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value when "
                f"ENVIRONMENT={self.ENVIRONMENT!r}"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
