"""embed-gateway — 三軌 embedding 統一 HTTP 介面（FastAPI）.

API 永遠打 http://embed-gateway:8081/embed；後端 (mlx / vllm / onnx) 由
EMBED_BACKEND env 決定，呼叫者不需要也不應該知道是哪一條。

Routes:
    GET  /health           liveness check
    POST /embed            { texts: [...], task_type: ... } → { embeddings: [...] }

跨後端 contract（必達）：
    - 回傳 dim = 1024
    - 同一段文字過三後端 cosine ≥ 0.99（保證 Qdrant collection 可攜）
"""
from __future__ import annotations

import os
from typing import Literal

from backends import mlx_proxy, onnx_runtime, vllm_proxy
from backends.onnx_runtime import ModelNotLoadedError
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BACKEND = os.getenv("EMBED_BACKEND", "onnx").lower()
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
EMBED_MODEL = os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")

app = FastAPI(title="memvault-embed-gateway", version="0.1.0")


class EmbedRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1)
    task_type: Literal["retrieval", "classification", "clustering", "similarity"] | None = None
    normalize: bool = True


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    dim: int
    backend: str


_ONNX_BACKEND_NAMES = {"onnx", "onnx_runtime", "cpu"}


@app.get("/health")
async def health() -> JSONResponse:
    body: dict = {
        "status": "ok",
        "backend": BACKEND,
        "model": EMBED_MODEL,
        "dim": EMBED_DIM,
    }
    if BACKEND in _ONNX_BACKEND_NAMES:
        detail = onnx_runtime.health_detail()
        body["onnx"] = detail
        if not detail["healthy"]:
            body["status"] = "error"
            body["reason"] = detail.get("reason", "model_not_loaded")
            body["hint"] = detail.get("hint", "run scripts/download-models.sh")
            return JSONResponse(status_code=503, content=body)
    return JSONResponse(status_code=200, content=body)


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    try:
        if BACKEND == "mlx_proxy":
            vectors = await mlx_proxy.forward(req.texts, req.task_type)
        elif BACKEND == "vllm_proxy":
            vectors = await vllm_proxy.forward(req.texts, req.task_type)
        elif BACKEND in _ONNX_BACKEND_NAMES:
            vectors = await onnx_runtime.embed(req.texts, req.task_type, normalize=req.normalize)
        else:
            raise HTTPException(status_code=500, detail=f"unknown backend: {BACKEND}")
    except HTTPException:
        raise
    except ModelNotLoadedError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "reason": "model_not_loaded",
                "backend": BACKEND,
                "error": str(exc),
                "hint": "run scripts/download-models.sh",
            },
        ) from exc
    except Exception as exc:  # pragma: no cover — surface upstream errors
        raise HTTPException(status_code=502, detail=f"backend {BACKEND} failed: {exc}") from exc

    if vectors and len(vectors[0]) != EMBED_DIM:
        raise HTTPException(
            status_code=500,
            detail=f"dim mismatch: backend returned {len(vectors[0])}, expected {EMBED_DIM}",
        )
    return EmbedResponse(
        embeddings=vectors,
        model=EMBED_MODEL,
        dim=EMBED_DIM,
        backend=BACKEND,
    )
