# memvault-api — multi-stage build (also serves as the worker image)
# Worker container 共用此 image，compose 覆蓋 CMD 為 `python -m apps.worker.main`
#
# Build context = apps/api/
# 注意：alembic/ 與 alembic.ini 由 Worker B 提供；scaffold 階段先放空殼。

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

COPY pyproject.toml ./
# 編一次 lock，獨立 layer 提高 cache 命中
RUN uv pip compile pyproject.toml -o requirements.lock \
    && uv pip install --system --requirement requirements.lock

# ---------- runtime stage ----------
FROM deps AS runtime

COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
