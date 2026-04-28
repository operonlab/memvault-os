"""Composable scoring stages for search result post-processing.

Each function takes a list of dicts with score/content/created_at keys
and returns the modified list. Modules mix and match as needed.

Used by: memvault (via ScoringPipeline delegation), available for any module.
"""

import math
from datetime import UTC, datetime


def apply_recency_boost(
    results: list[dict],
    *,
    created_at_key: str = "created_at",
    score_key: str = "score",
    half_life_days: float = 14.0,
    weight: float = 0.15,
    now: datetime | None = None,
) -> list[dict]:
    """Boost scores for newer results using exponential decay.

    score *= 1.0 + weight * exp(-age / half_life)
    """
    now = now or datetime.now(UTC)
    for r in results:
        created_at = r.get(created_at_key)
        if created_at:
            age_days = max((now - created_at).total_seconds() / 86400, 0)
            boost = 1.0 + weight * math.exp(-age_days / half_life_days)
            r[score_key] *= boost
    return results


def apply_min_score_filter(
    results: list[dict],
    *,
    min_score: float = 0.1,
    score_key: str = "score",
) -> list[dict]:
    """Remove results below score threshold."""
    return [r for r in results if r.get(score_key, 0) >= min_score]


def apply_length_normalization(
    results: list[dict],
    *,
    content_key: str = "content",
    score_key: str = "score",
    anchor_length: int = 500,
    penalty_factor: float = 0.3,
) -> list[dict]:
    """Normalize scores based on content length.

    Penalizes both very short and very long content relative to anchor.
    score *= 1.0 / (1.0 + penalty_factor * |log2(len/anchor)|)
    """
    for r in results:
        content = r.get(content_key, "")
        content_len = max(len(content), 1)
        ratio = content_len / anchor_length
        r[score_key] /= 1.0 + penalty_factor * abs(math.log2(ratio))
    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
