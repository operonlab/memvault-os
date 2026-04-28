"""config_stub — minimal Settings for memvault-os standalone deployment.

Replaces monorepo's `src.config`. Env prefix: `MEMVAULT_`.

Usage in memvault code (unchanged):
    from src.config import settings
    db = settings.db_url
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Standalone settings — accepts BOTH `MEMVAULT_*` (preferred) and the
    short, unprefixed names compose passes (e.g. `DATABASE_URL`, `REDIS_URL`).
    Fixed by codex review (config_stub aliases) — without these aliases,
    compose env was being silently ignored and Settings fell back to localhost.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEMVAULT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 10000
    debug: bool = False

    # Database & Cache — accept both MEMVAULT_DB_URL and DATABASE_URL
    db_url: str = Field(
        default="postgresql+asyncpg://memvault:memvault@localhost:5432/memvault",
        validation_alias=AliasChoices("MEMVAULT_DB_URL", "DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("MEMVAULT_REDIS_URL", "REDIS_URL"),
    )

    # Vector store
    qdrant_url: str = Field(
        default="http://localhost:6333",
        validation_alias=AliasChoices("MEMVAULT_QDRANT_URL", "QDRANT_URL"),
    )
    qdrant_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("MEMVAULT_QDRANT_API_KEY", "QDRANT_API_KEY"),
    )

    # Embedding service — default updated to embed-gateway 1024d
    embed_base_url: str = Field(
        default="http://embed-gateway:8081",
        validation_alias=AliasChoices("MEMVAULT_EMBED_BASE_URL", "EMBED_BASE_URL"),
    )
    embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embed_dim: int = 1024

    # LLM (LiteLLM proxy)
    litellm_base: str = Field(
        default="http://litellm:4000/v1",
        validation_alias=AliasChoices("MEMVAULT_LITELLM_BASE", "LITELLM_BASE"),
    )
    litellm_key: str = Field(
        default="",
        validation_alias=AliasChoices("MEMVAULT_LITELLM_KEY", "LITELLM_KEY", "LITELLM_MASTER_KEY"),
    )
    llm_default_model: str = "gpt-4o-mini"

    # Security
    secret_key: str = "change-me-in-production"
    internal_api_key: str = ""

    # Event bus
    event_backend: str = "memory"  # "memory" only in OS v1

    # Lifecycle / tier toggles
    audit_enabled: bool = True
    frozen_tier_enabled: bool = False
    cold_tier_enabled: bool = False

    # S3 (optional, for frozen tier)
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_archive_bucket: str = "memvault-archive"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Capabilities
    mlx_enabled: bool = False  # OS default: assume non-Apple-Silicon

    def validate_secret_key(self) -> None:
        if self.secret_key == "change-me-in-production":
            raise ValueError(
                "MEMVAULT_SECRET_KEY is set to the default; set a secure value."
            )


settings = Settings()
