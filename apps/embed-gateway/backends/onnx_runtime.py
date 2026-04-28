"""onnx_runtime — CPU fallback embedding backend.

實作策略（v3.2）：
    主路：自寫 Qwen3-Embedding-0.6B ONNX wrapper（1024d）
    備援：mxbai-embed-large-v1（中英效果需重測，整 corpus 重 reindex）

Fail-closed：模型缺失時 backend 進入 unhealthy，/health 回 503，
/embed 拒絕產生零向量（污染 Qdrant），呼叫者看到 503 後可跑
`scripts/download-models.sh`。
"""
from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

LOG = logging.getLogger("embed-gateway.onnx")

EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
MODEL_DIR = os.getenv("ONNX_MODEL_DIR", "/models/qwen3-embedding-0.6b")
MAX_LENGTH = int(os.getenv("ONNX_MAX_LENGTH", "512"))

_session: Any | None = None
_tokenizer: Any | None = None
_load_error: str | None = None


class ModelNotLoadedError(RuntimeError):
    """Raised when ONNX model artifacts are missing or failed to load."""


def _model_paths() -> tuple[str, str]:
    return (
        os.path.join(MODEL_DIR, "model.onnx"),
        os.path.join(MODEL_DIR, "tokenizer.json"),
    )


def _try_load() -> bool:
    """Lazy-load ONNX session + tokenizer. Returns True if loaded."""
    global _session, _tokenizer, _load_error
    if _session is not None:
        return True

    model_path, tokenizer_path = _model_paths()
    if not (os.path.isfile(model_path) and os.path.isfile(tokenizer_path)):
        _load_error = (
            f"model artifacts missing under {MODEL_DIR} "
            "(expected model.onnx + tokenizer.json) — "
            "run scripts/download-models.sh"
        )
        LOG.warning("ONNX backend unhealthy: %s", _load_error)
        return False

    try:
        import onnxruntime as ort  # type: ignore
        from tokenizers import Tokenizer  # type: ignore

        _session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(tokenizer_path)
        _tokenizer.enable_truncation(max_length=MAX_LENGTH)
        _tokenizer.enable_padding(length=MAX_LENGTH)
        _load_error = None
        LOG.info("loaded ONNX model from %s", MODEL_DIR)
        return True
    except Exception as exc:  # pragma: no cover
        _load_error = f"failed to load ONNX session: {exc}"
        LOG.exception("ONNX backend unhealthy: %s", _load_error)
        return False


def is_healthy() -> bool:
    """Probe at startup / health endpoint. Caches result via module globals."""
    return _try_load()


def health_detail() -> dict[str, Any]:
    healthy = is_healthy()
    detail: dict[str, Any] = {
        "healthy": healthy,
        "model_dir": MODEL_DIR,
    }
    if not healthy:
        detail["reason"] = "model_not_loaded"
        detail["error"] = _load_error or "unknown"
        detail["hint"] = "run scripts/download-models.sh"
    return detail


def _mean_pool_normalize(last_hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    mask = mask[..., None].astype(last_hidden.dtype)
    summed = (last_hidden * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1e-9)
    pooled = summed / counts
    norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
    return pooled / norms


async def embed(
    texts: list[str], task_type: str | None, *, normalize: bool = True
) -> list[list[float]]:
    if not _try_load():
        raise ModelNotLoadedError(
            _load_error or "ONNX model not loaded; run scripts/download-models.sh"
        )

    assert _session is not None and _tokenizer is not None
    encs = _tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encs], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)

    inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
    outputs = _session.run(None, inputs)
    last_hidden = outputs[0]
    pooled = _mean_pool_normalize(last_hidden, attention_mask) if normalize else last_hidden.mean(1)
    return pooled.astype(float).tolist()
