"""vllm_proxy — forward embed requests to vLLM container (GPU profile).

vLLM exposes OpenAI-compatible /v1/embeddings endpoint when launched with
`--task embed`. We adapt the request shape and normalize the response.
"""
from __future__ import annotations

import os

import httpx

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://vllm:8000/v1")
VLLM_MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen3-Embedding-0.6B")
TIMEOUT = float(os.getenv("VLLM_TIMEOUT", "60"))


async def forward(texts: list[str], task_type: str | None) -> list[list[float]]:
    # vLLM /v1/embeddings 接受 OpenAI 格式 (input + model)
    payload = {"input": texts, "model": VLLM_MODEL}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{VLLM_BASE_URL}/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()
    # response shape: {"data": [{"index": int, "embedding": [...]}, ...]}
    items = sorted(data["data"], key=lambda d: d["index"])
    return [item["embedding"] for item in items]
