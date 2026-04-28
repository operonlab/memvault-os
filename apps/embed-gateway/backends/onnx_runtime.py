"""onnx_runtime — CPU fallback embedding backend.

實作策略（v3.2）：
    主路：自寫 Qwen3-Embedding-0.6B ONNX wrapper（1024d）
    備援：mxbai-embed-large-v1（中英效果需重測，整 corpus 重 reindex）

scaffold 階段：先放 stub，回零向量但維持正確 shape，方便整條 plumbing
打通。實際 ONNX session 載入由 Worker A / 後續 ticket 補。

未來實作要點：
    1. ONNX 模型放 /models/qwen3-embed-0.6b-onnx/，由 image 內或 volume 提供
    2. 用 tokenizers.Tokenizer.from_file() 載 tokenizer
    3. ort.InferenceSession 設 CPUExecutionProvider
    4. mean pooling + L2 normalize → 對齊 MLX 輸出（cosine ≥ 0.99）
"""
from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

LOG = logging.getLogger("embed-gateway.onnx")

EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
MODEL_DIR = os.getenv("ONNX_MODEL_DIR", "/models/qwen3-embed-0.6b-onnx")
MAX_LENGTH = int(os.getenv("ONNX_MAX_LENGTH", "512"))

_session: Any | None = None
_tokenizer: Any | None = None


def _try_load() -> bool:
    """Lazy-load ONNX session + tokenizer. Returns True if real model loaded."""
    global _session, _tokenizer
    if _session is not None:
        return True

    model_path = os.path.join(MODEL_DIR, "model.onnx")
    tokenizer_path = os.path.join(MODEL_DIR, "tokenizer.json")
    if not (os.path.isfile(model_path) and os.path.isfile(tokenizer_path)):
        LOG.warning("ONNX model not found at %s — stub mode", MODEL_DIR)
        return False

    try:
        import onnxruntime as ort  # type: ignore
        from tokenizers import Tokenizer  # type: ignore

        _session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        _tokenizer = Tokenizer.from_file(tokenizer_path)
        _tokenizer.enable_truncation(max_length=MAX_LENGTH)
        _tokenizer.enable_padding(length=MAX_LENGTH)
        LOG.info("loaded ONNX model from %s", MODEL_DIR)
        return True
    except Exception:  # pragma: no cover
        LOG.exception("failed to load ONNX model — stub mode")
        return False


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
        # stub fallback：保持 plumbing 可動
        return [[0.0] * EMBED_DIM for _ in texts]

    assert _session is not None and _tokenizer is not None
    encs = _tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encs], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)

    inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
    outputs = _session.run(None, inputs)
    last_hidden = outputs[0]
    pooled = _mean_pool_normalize(last_hidden, attention_mask) if normalize else last_hidden.mean(1)
    return pooled.astype(float).tolist()
