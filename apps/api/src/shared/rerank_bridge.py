"""Jina Reranker v3 MLX bridge — persistent subprocess for cross-encoder reranking.

Manages a long-running rerank worker process (Jina Reranker v3, 0.6B params).
The worker loads the model once and serves requests via stdin/stdout JSON lines.
Falls back gracefully: returns None when worker is unavailable.
"""

import asyncio
import json
import logging
import subprocess
import threading
from pathlib import Path

try:
    from sdk_client.retry import async_with_backoff as _async_with_backoff
    from sdk_client.timeout import dynamic_timeout as _dynamic_timeout
    _HAS_RETRY = True
except ImportError:
    _HAS_RETRY = False

logger = logging.getLogger(__name__)

OMLX_VENV = Path.home() / ".venvs" / "omlx"
WORKER_SCRIPT = OMLX_VENV / "rerank_worker.py"
PYTHON = OMLX_VENV / "bin" / "python3"

_process: subprocess.Popen | None = None
_startup_lock = asyncio.Lock()  # protects worker process startup
_io_lock = asyncio.Lock()  # protects stdin/stdout I/O atomicity
_ready = False


def _drain_stderr(proc: subprocess.Popen) -> None:
    """Consume stderr to prevent pipe buffer deadlock."""
    try:
        for line in proc.stderr:
            if line.strip():
                logger.debug("rerank worker stderr: %s", line.rstrip())
    except Exception:  # noqa: S110 — stderr drain; pipe closure is expected
        pass


async def _ensure_worker() -> bool:
    """Start the rerank worker process if not running."""
    global _process, _ready

    async with _startup_lock:
        if _process is not None and _process.poll() is None and _ready:
            return True

        if not PYTHON.exists() or not WORKER_SCRIPT.exists():
            logger.warning("oMLX venv or rerank worker not found at %s", OMLX_VENV)
            return False

        try:
            _process = subprocess.Popen(  # noqa: ASYNC220, S603
                [str(PYTHON), str(WORKER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            loop = asyncio.get_event_loop()
            line = await asyncio.wait_for(
                loop.run_in_executor(None, _process.stdout.readline),
                timeout=60,  # Model load can take a few seconds
            )
            if not line:
                logger.warning("Rerank worker produced no output")
                _process.kill()
                _process = None
                return False

            # Drain stderr in background to prevent pipe buffer deadlock
            threading.Thread(target=_drain_stderr, args=(_process,), daemon=True).start()

            status = json.loads(line.strip())
            if status.get("status") == "ready":
                _ready = True
                logger.info("Rerank worker ready: %s", status.get("model"))
                return True

            logger.warning("Rerank worker unexpected status: %s", status)
            _process.kill()
            _process = None
            return False
        except Exception as e:
            logger.warning("Failed to start rerank worker: %s", e)
            if _process:
                try:
                    _process.kill()
                except ProcessLookupError:
                    pass
            _process = None
            _ready = False
            return False


async def _send_request(request: dict) -> dict | None:
    """Send a JSON request to worker and read response."""
    global _process, _ready

    if not await _ensure_worker():
        return None

    async with _io_lock:
        try:
            line = json.dumps(request) + "\n"
            loop = asyncio.get_event_loop()

            def _write_request() -> None:
                _process.stdin.write(line)
                _process.stdin.flush()

            await asyncio.wait_for(
                loop.run_in_executor(None, _write_request),
                timeout=10,
            )

            response_line = await asyncio.wait_for(
                loop.run_in_executor(None, _process.stdout.readline),
                timeout=30,
            )

            if not response_line:
                logger.warning("Rerank worker returned empty response")
                _ready = False
                return None

            result = json.loads(response_line.strip())
            if "error" in result:
                logger.warning("Rerank worker error: %s", result["error"])
                return None

            return result

        except TimeoutError:
            logger.warning("Rerank worker request timed out")
            _ready = False
            if _process:
                try:
                    _process.kill()
                except ProcessLookupError:
                    pass
                _process = None
            return None
        except Exception:
            logger.exception("Rerank bridge communication error")
            _ready = False
            return None


async def shutdown():
    """Gracefully shutdown the rerank worker process."""
    global _process, _ready
    if _process and _process.poll() is None:
        try:
            _process.stdin.close()
            _process.wait(timeout=5)
        except Exception:
            _process.kill()
    _process = None
    _ready = False


async def rerank(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[dict] | None:
    """Rerank documents using Jina cross-encoder.

    Returns list of {"index": int, "score": float} sorted by relevance,
    or None if worker is unavailable.
    """
    if not documents:
        return []

    request = {"query": query, "documents": documents}
    if top_n is not None:
        request["top_n"] = top_n

    result = await _send_request(request)
    if result is None:
        return None

    return result.get("scores")
