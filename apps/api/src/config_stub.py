"""config_stub — minimal Settings for memvault-os standalone deployment.

Replaces monorepo's `src.config`. Env prefix: `MEMVAULT_`.

Usage in memvault code (unchanged):
    from src.config import settings
    db = settings.db_url
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMVAULT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 10000
    debug: bool = False

    # Database & Cache
    db_url: str = "postgresql+asyncpg://memvault:memvault@localhost:5432/memvault"
    redis_url: str = "redis://localhost:6379/0"

    # Vector store
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # Embedding service
    embed_base_url: str = "http://localhost:11434"  # Ollama-compatible endpoint
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    # LLM (LiteLLM proxy)
    litellm_base: str = "http://localhost:4000"
    litellm_key: str = ""
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
