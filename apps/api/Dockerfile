# memvault-api — multi-stage build (also serves as the worker image)
# Worker container 共用此 image，compose 覆蓋 CMD 為 `python -m apps.worker.main`
#
# Build context = repo root (..). The compose file passes `context: ..` for
# both api and worker services so this Dockerfile can COPY apps/worker/ into
# the image — without that, `python -m apps.worker.main` errors with
# ModuleNotFoundError and the worker container restart-loops.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# ---------- deps stage ----------
FROM base AS deps

COPY apps/api/pyproject.toml ./
# 編一次 lock，獨立 layer 提高 cache 命中
RUN uv pip compile pyproject.toml -o requirements.lock \
    && uv pip install --system --requirement requirements.lock

# ---------- runtime stage ----------
FROM deps AS runtime

COPY apps/api/src/ ./src/
COPY apps/api/alembic/ ./alembic/
COPY apps/api/alembic.ini ./alembic.ini
# Worker source — same image hosts both api and worker (different CMDs).
# `python -m apps.worker.main` resolves under /app/apps/worker/main.py.
COPY apps/worker/ ./apps/worker/

ENV PORT=8080 \
    PYTHONPATH=/app:/app/src
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8080/health/liveliness || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
