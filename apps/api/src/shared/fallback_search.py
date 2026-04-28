"""Unified PostgreSQL fallback search — CJK-aware keyword + RRF fusion.

Used when Qdrant is unavailable or returns empty results. Provides:
  - CJK-aware keyword search (jieba multi-term instead of ILIKE)
  - BM25-lite scoring for matched results
  - RRF fusion for combining vector + keyword results
  - Configurable tier scoring

Used by: memvault, intelflow (and any future module with search).
"""

import logging
from dataclasses import dataclass

from sqlalchemy import Column, or_

from .text_utils import compute_keyword_score, is_cjk, jieba_tokenize

logger = logging.getLogger(__name__)


# --- Tier Scoring Configuration ---


@dataclass(frozen=True)
class TierScoring:
    """Scoring weights per data tier.

    hot_base: Base score for hot-tier text matches (full index coverage).
    warm_decay: Multiplier for warm-tier scores (hot_base * warm_decay).
    cold_decay: Multiplier for cold-tier scores (hot_base * cold_decay).
    """

    hot_base: float = 0.5
    warm_decay: float = 0.7
    cold_decay: float = 0.6

    @property
    def warm_score(self) -> float:
        return self.hot_base * self.warm_decay

    @property
    def cold_score(self) -> float:
        return self.hot_base * self.cold_decay


# Default tier scoring — used when no override provided
DEFAULT_TIER_SCORING = TierScoring()


# --- CJK-Aware Query Building ---


def build_ilike_conditions(
    query: str,
    *columns: Column,
    min_token_len: int = 1,
) -> list:
    """Build SQLAlchemy ILIKE conditions using jieba tokenization for CJK.

    For CJK text: tokenizes with jieba, builds multi-term OR across columns.
    For English text: uses standard ILIKE with the full query.

    Returns a list of SQLAlchemy conditions to be combined with AND/OR.

    Example:
        Query "port 衝突" → tokens ["port", "衝突"]
        → (col.ilike('%port%') | col.ilike('%衝突%'))
        Instead of: col.ilike('%port 衝突%') which requires exact substring.
    """
    if is_cjk(query):
        tokens = jieba_tokenize(query)
        if not tokens:
            return [col.ilike(f"%{query}%") for col in columns]

        # Filter short tokens
        tokens = [t for t in tokens if len(t) >= min_token_len]
        if not tokens:
            tokens = jieba_tokenize(query, remove_stopwords=False)

        # Build: for each token, match ANY column → then AND across tokens
        # This finds documents that mention ALL query terms somewhere
        token_conditions = []
        for token in tokens:
            escaped = token.replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            col_match = or_(*[col.ilike(pattern) for col in columns])
            token_conditions.append(col_match)

        return token_conditions  # caller should AND these
    else:
        # English: single ILIKE with full query
        escaped = query.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        return [or_(*[col.ilike(pattern) for col in columns])]


def score_text_match(
    query: str,
    text: str,
    tier: str = "hot",
    scoring: TierScoring | None = None,
    avgdl: int = 100,
) -> float:
    """Score a text match using BM25-lite + tier weighting.

    Args:
        query: The search query.
        text: The matched document text.
        tier: "hot", "warm", or "cold".
        scoring: Tier scoring config (defaults to DEFAULT_TIER_SCORING).
        avgdl: Average document length for BM25 normalization.

    Returns:
        Score in [0, 1] range.
    """
    scoring = scoring or DEFAULT_TIER_SCORING

    # Compute BM25-lite score
    tokens = jieba_tokenize(query)
    if not tokens:
        # Fallback: use tier base score
        if tier == "warm":
            return scoring.warm_score
        elif tier == "cold":
            return scoring.cold_score
        return scoring.hot_base

    bm25_score = compute_keyword_score(tokens, text, avgdl=avgdl)

    # Apply tier weighting
    if tier == "warm":
        return bm25_score * scoring.warm_decay
    elif tier == "cold":
        return bm25_score * scoring.cold_decay
    return bm25_score


# --- RRF Fusion ---


def rrf_fuse_scores(
    *result_lists: list[tuple[str, float]],
    k: int = 60,
    boosts: list[float] | None = None,
) -> dict[str, float]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    Args:
        result_lists: Each list contains (entity_id, score) tuples, sorted by score desc.
        k: RRF smoothing constant (default 60).
        boosts: Per-list boost multiplier (default all 1.0).

    Returns:
        {entity_id: fused_score} dict, sorted by fused score desc.
    """
    if boosts is None:
        boosts = [1.0] * len(result_lists)

    scores: dict[str, float] = {}
    for result_list, boost in zip(result_lists, boosts, strict=True):
        for rank, (entity_id, _score) in enumerate(result_list):
            scores[entity_id] = scores.get(entity_id, 0) + boost / (k + rank)

    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


# --- Per-service avg document length ---
# Re-exported from search_constants for backward compatibility.
from .search_constants import SERVICE_AVGDL as SERVICE_AVGDL  # noqa: E402
from .search_constants import get_avgdl as get_avgdl  # noqa: E402
