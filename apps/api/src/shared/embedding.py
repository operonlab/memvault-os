"""Embedding service — HTTP client to embed-gateway (memvault-os standalone).

Replaces monorepo's oMLX subprocess bridge with a simple HTTP POST.
The gateway is expected to expose:

    POST {EMBED_BASE_URL}/embed
        {"texts": ["...", "..."], "task_type": "search_query" | None}
    -> {"embeddings": [[float, ...], ...]}

Configurable via MEMVAULT_EMBED_BASE_URL / MEMVAULT_EMBED_DIM (default 1024).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.config_stub import settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024  # Qwen3-Embedding-0.6B output dimension

__all__ = ["EMBEDDING_DIM", "get_embedding", "get_embeddings_batch"]

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def _get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    base_url=settings.embed_base_url.rstrip("/"),
                    timeout=_TIMEOUT,
                )
    return _client


async def _post_embed(texts: list[str], task_type: str | None) -> list[list[float] | None]:
    if not texts:
        return []
    client = await _get_http_client()
    payload: dict = {"texts": texts}
    if task_type:
        payload["task_type"] = task_type
    try:
        resp = await client.post("/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
        embeds = data.get("embeddings") or []
        result: list[list[float] | None] = []
        for vec in embeds:
            if isinstance(vec, list) and vec:
                result.append([float(x) for x in vec])
            else:
                result.append(None)
        # Pad if gateway returned fewer
        while len(result) < len(texts):
            result.append(None)
        return result
    except Exception as e:
        logger.warning("embed-gateway request failed: %s", e)
        return [None] * len(texts)


async def get_embedding(text: str, task_type: str | None = None) -> list[float] | None:
    """Generate embedding vector via embed-gateway.

    Returns None when the gateway is unreachable (graceful degradation).
    """
    if not text:
        return None
    results = await _post_embed([text], task_type)
    return results[0] if results else None


async def get_embeddings_batch(
    texts: list[str],
    task_type: str | None = None,
) -> list[list[float] | None]:
    """Generate embeddings for multiple texts in a single HTTP call."""
    if not texts:
        return []
    return await _post_embed(texts, task_type)
