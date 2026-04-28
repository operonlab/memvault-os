"""mlx_proxy — forward embed requests to host MLX sidecar (macOS only).

Container 內透過 host.docker.internal:18081 連到 host 上的
infra/mlx-sidecar/embed_worker.py（由 launchd 管）。
"""
from __future__ import annotations

import os

import httpx

MLX_HOST_URL = os.getenv("MLX_HOST_URL", "http://host.docker.internal:18081")
TIMEOUT = float(os.getenv("MLX_TIMEOUT", "30"))


async def forward(texts: list[str], task_type: str | None) -> list[list[float]]:
    payload = {"texts": texts, "task_type": task_type}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{MLX_HOST_URL}/embed", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["embeddings"]
