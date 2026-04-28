"""Single source of truth for search-related constants."""

EMBEDDING_DIM = 1024  # Qwen3-Embedding-0.6B output dimension

# Per-service average document length (token count) for BM25 normalization
SERVICE_AVGDL: dict[str, int] = {
    "memvault": 80,
    "intelflow": 500,
    "taskflow": 120,
    "finance": 60,
    "capture": 150,
    "dailyos": 100,
    "default": 200,
}


def get_avgdl(service: str) -> int:
    """Get average document length for a service."""
    return SERVICE_AVGDL.get(service, SERVICE_AVGDL["default"])
