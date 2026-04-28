"""MLX embedding sidecar — runs on macOS host (Apple Silicon).

跑在 host 而非 container：MLX 需要 Metal GPU 直存，Docker Desktop on Mac
是 Linux VM 沒有 Metal 權限。

由 launchd LaunchAgent 啟動，bind 127.0.0.1:18081；container 內的
embed-gateway 透過 host.docker.internal:18081 轉發到這支。

Protocol:
    POST /embed
    Request: {"texts": [str, ...], "task_type": "retrieval" | "classification" | ...}
    Response: {"embeddings": [[float, ...], ...], "model": str, "dim": 1024}

只用 stdlib（http.server）+ mlx-embeddings，不引入 FastAPI 以減少冷啟動時間。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LOG = logging.getLogger("mlx-embed-sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MODEL_NAME = os.getenv("MLX_EMBED_MODEL", "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ")
HOST = os.getenv("MLX_HOST", "127.0.0.1")
PORT = int(os.getenv("MLX_PORT", "18081"))
EMBED_DIM = 1024

_model = None
_tokenizer = None


def _load_model() -> None:
    global _model, _tokenizer
    if _model is not None:
        return
    try:
        from mlx_embeddings.utils import load  # type: ignore

        _model, _tokenizer = load(MODEL_NAME)
        LOG.info("loaded model=%s dim=%d", MODEL_NAME, EMBED_DIM)
    except Exception:  # pragma: no cover — stub path
        LOG.exception("failed to load mlx model; running in stub mode")
        _model = None
        _tokenizer = None


def _embed(texts: list[str], task_type: str | None) -> list[list[float]]:
    _load_model()
    if _model is None or _tokenizer is None:
        # stub fallback：回零向量，方便 plumbing 期單測
        return [[0.0] * EMBED_DIM for _ in texts]

    import mlx.core as mx  # type: ignore

    inputs = _tokenizer.batch_encode_plus(
        texts, return_tensors="mlx", padding=True, truncation=True, max_length=512
    )
    outputs = _model(inputs["input_ids"], attention_mask=inputs["attention_mask"])
    vectors = outputs.text_embeds  # already L2-normalized by mlx-embeddings
    return mx.array(vectors).tolist()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _write_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write_json(200, {"status": "ok", "model": MODEL_NAME, "dim": EMBED_DIM})
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/embed":
            self._write_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._write_json(400, {"error": "invalid json"})
            return
        texts = payload.get("texts") or []
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            self._write_json(400, {"error": "texts must be list[str]"})
            return
        task_type = payload.get("task_type")
        try:
            vectors = _embed(texts, task_type)
        except Exception as exc:  # pragma: no cover
            LOG.exception("embed failed")
            self._write_json(500, {"error": str(exc)})
            return
        self._write_json(200, {"embeddings": vectors, "model": MODEL_NAME, "dim": EMBED_DIM})


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    LOG.info("listening on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
