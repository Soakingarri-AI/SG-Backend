"""Centralized, typed application settings.

Loaded once at import and injected everywhere via ``get_settings()``. In AWS,
values arrive from ECS task definition secrets / SSM Parameter Store rather
than a committed ``.env`` file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    COOKIE_DOMAIN: str = ".soakingarri.com"

    # --- CORS ---
    CORS_ORIGINS: list[str] = Field(default_factory=list)

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

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
