"""Memvault LLM configuration — PydanticAI model factories.

Backends:
  - LiteLLM (localhost:4000): proxy to external models (primary)
  - DeepSeek (external): configurable via env vars (kg_auto_evolve)
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

# ── Backend endpoints ──
_LITELLM_BASE = "http://localhost:4000/v1"
_LITELLM_KEY = "sk-litellm-local-dev"  # nosec — local dev proxy key

# ── LiteLLM model resolution ──
_MODEL_CANDIDATES = [
    "gemini-3.1-flash-lite",
    "kimi-k2.5",
    "minimax-m2.7-hs",
    "deepseek-v3",
    "qwen3.5-flash",
    "grok-4.1-fast",
    "gemini-3.1-flash",
]

_cached_model: str | None = None
_cached_model_ts: float = 0.0
_CACHE_TTL = 60.0


async def resolve_model(
    base_url: str = _LITELLM_BASE,
    api_key: str = _LITELLM_KEY,
    candidates: list[str] | None = None,
) -> str:
    """Pick first available model from candidates via LiteLLM /v1/models. Cached 60s."""
    global _cached_model, _cached_model_ts
    now = time.monotonic()
    if _cached_model and (now - _cached_model_ts) < _CACHE_TTL:
        return _cached_model

    cands = candidates or _MODEL_CANDIDATES
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            available = {m["id"] for m in resp.json().get("data", [])}
            for c in cands:
                if c in available:
                    _cached_model = c
                    _cached_model_ts = now
                    return c
    except Exception:
        logger.debug("resolve_model: failed to query LiteLLM /models, using default")

    _cached_model = cands[0]
    _cached_model_ts = now
    return _cached_model


# ── Model factories ──


def make_litellm_model(
    model_name: str,
    base_url: str = _LITELLM_BASE,
    api_key: str = _LITELLM_KEY,
) -> OpenAIChatModel:
    """Create an OpenAIChatModel for a specific LiteLLM proxy model (port 4000)."""
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)


async def get_litellm_model() -> OpenAIChatModel:
    """Resolve best available LiteLLM model + create OpenAIChatModel."""
    name = await resolve_model()
    return make_litellm_model(name)


def make_deepseek_model() -> OpenAIChatModel:
    """Create an OpenAIChatModel for DeepSeek external API (env-configurable)."""
    base_url = os.environ.get("KG_AUTO_EVOLVE_LLM_URL", "https://api.deepseek.com/v1")
    api_key = os.environ.get("KG_AUTO_EVOLVE_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    model_name = os.environ.get("KG_AUTO_EVOLVE_MODEL", "deepseek-chat")
    # Strip /chat/completions suffix if present (legacy full-URL format)
    if base_url.endswith("/chat/completions"):
        base_url = base_url.rsplit("/chat/completions", 1)[0]
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)
