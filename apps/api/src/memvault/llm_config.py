"""Memvault LLM configuration — PydanticAI model factories.

Backends:
  - LiteLLM (proxy): URL+key read from LITELLM_BASE / LITELLM_KEY env vars.
    In docker-compose stacks this resolves to ``http://litellm:4000/v1`` and
    the master key from ``.env``. The hard-coded ``localhost:4000`` default
    is only used for `pytest` outside compose; production never reads it.
  - DeepSeek (external): configurable via env vars (kg_auto_evolve)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

# ── Backend endpoints ──
# Read from env so docker-compose can inject the in-cluster service name.
# Fallback `localhost:4000` only valid for local pytest / dev shell.
_LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://litellm:4000/v1")
_LITELLM_KEY = os.environ.get("LITELLM_KEY", "sk-litellm-local-dev")  # nosec — local dev proxy key

# Retry budget for litellm cold-start / 503 storms. 1+2+4+8+16 = 31s total.
_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 16.0)


async def litellm_post_with_retry(
    path: str,
    *,
    json: dict,
    headers: dict | None = None,
    timeout: float = 30.0,
    base_url: str | None = None,
) -> httpx.Response:
    """POST to LiteLLM with exponential backoff on connection-refused / 503.

    Retries up to 5 times (1, 2, 4, 8, 16 seconds). Re-raises on the final
    failure so callers can translate to HTTP 503 for the user; the worker
    process itself never crashes from a transient LiteLLM hiccup.
    """
    base = base_url or _LITELLM_BASE
    url = base.rstrip("/") + "/" + path.lstrip("/")
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt, delay in enumerate((0.0, *_RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                resp = await client.post(url, json=json, headers=headers or {})
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_exc = exc
                logger.warning(
                    "litellm.post: connection error attempt=%s err=%s", attempt, exc
                )
                continue
            if resp.status_code == 503:
                logger.warning("litellm.post: 503 attempt=%s", attempt)
                last_exc = httpx.HTTPStatusError(
                    "503 from LiteLLM", request=resp.request, response=resp
                )
                continue
            return resp
    assert last_exc is not None
    raise last_exc

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
