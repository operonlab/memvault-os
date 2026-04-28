"""Qdrant connection management — memvault-os standalone.

Provides BOTH:
  - get_qdrant_client() — sync, returns AsyncQdrantClient instance (lazy init,
    no health-check). Used by memvault.kg_auto_evolve and qdrant_search.scroll_by_service.
  - get_client() — async, performs first-call health check via get_collections().
  - is_available() — async health check.
"""

import logging
import time

from qdrant_client import AsyncQdrantClient

from src.config_stub import settings

logger = logging.getLogger(__name__)

_RETRY_INTERVAL = 5

_client: AsyncQdrantClient | None = None
_available: bool | None = None
_last_failure: float = 0


def _build_client() -> AsyncQdrantClient | None:
    """Construct AsyncQdrantClient from settings.qdrant_url (sync, no I/O)."""
    try:
        kwargs: dict = {"url": settings.qdrant_url, "timeout": 10}
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        return AsyncQdrantClient(**kwargs)
    except Exception as e:
        logger.warning("Failed to build Qdrant client: %s", e)
        return None


def get_qdrant_client() -> AsyncQdrantClient | None:
    """Sync accessor — returns cached client or constructs a new one.

    Does NOT perform a health check. Callers must handle network errors
    on the actual operation (.scroll, .upsert, etc.).
    """
    global _client, _available, _last_failure

    if _available is False and time.monotonic() - _last_failure < _RETRY_INTERVAL:
        return None

    if _client is None:
        _client = _build_client()
        if _client is None:
            _available = False
            _last_failure = time.monotonic()
            return None
    return _client


async def get_client() -> AsyncQdrantClient | None:
    """Async accessor — returns cached client and performs first-call health check."""
    global _client, _available, _last_failure

    if _available is False:
        if time.monotonic() - _last_failure < _RETRY_INTERVAL:
            return None
        _available = None
        _client = None

    if _client is not None and _available is True:
        return _client

    client = get_qdrant_client()
    if client is None:
        return None

    try:
        await client.get_collections()
        _available = True
        logger.info("Qdrant client connected at %s", settings.qdrant_url)
        return client
    except Exception as e:
        logger.warning("Qdrant unavailable: %s — will retry in %ds", e, _RETRY_INTERVAL)
        _available = False
        _last_failure = time.monotonic()
        _client = None
        return None


async def is_available() -> bool:
    """Check if Qdrant is reachable."""
    global _available
    if _available is None:
        await get_client()
    return _available or False


async def reset() -> None:
    """Reset client state — used for reconnection attempts."""
    global _client, _available
    if _client is not None:
        try:
            await _client.close()
        except Exception:  # noqa: S110
            pass
    _client = None
    _available = None


async def health_check() -> dict:
    """Return health status for monitoring."""
    try:
        client = await get_client()
        if client is None:
            return {"status": "unavailable", "error": "client not connected"}
        collections = await client.get_collections()
        return {
            "status": "healthy",
            "collections": len(collections.collections),
            "url": settings.qdrant_url,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
