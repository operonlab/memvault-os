# memvault-embed-gateway — 三軌 embedding 統一 HTTP 介面
# 後端由 EMBED_BACKEND env 切換：onnx (default) / vllm_proxy / mlx_proxy

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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "pydantic>=2.8" \
        "httpx>=0.27" \
        "onnxruntime>=1.20" \
        "tokenizers>=0.20" \
        "numpy>=1.26"

COPY server.py ./server.py
COPY backends/ ./backends/

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD curl -f http://localhost:8081/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8081"]
