"""Cross-encoder reranking for memvault.

Uses Jina Reranker v3 (0.6B, MLX-native) via persistent subprocess worker.
True cross-encoder: query and document are jointly encoded with cross-attention,
producing more accurate relevance scores than bi-encoder cosine similarity.

Includes circuit breaker for graceful degradation and attention-gated computation
to skip the expensive cross-encoder when scoring pipeline output is already confident.
Pattern inspired by TurboQuant+ Sparse V (ICLR 2026).
"""

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_GATE_FORCE_DISABLED = os.environ.get("MEMVAULT_RERANKER_GATE_DISABLED", "").lower() in (
    "1",
    "true",
    "yes",
)


@dataclass
class RerankerWeights:
    """Original vs cross-encoder blend weights."""

    original: float = 0.3
    rerank: float = 0.7


# Intent-dependent reranker blend (AttnRes-inspired)
INTENT_RERANKER_WEIGHTS: dict[str, RerankerWeights] = {
    # Entity lookup → cross-encoder excels at entity matching
    "entity_lookup": RerankerWeights(original=0.2, rerank=0.8),
    # Conceptual → original embedding captures conceptual similarity better
    "conceptual": RerankerWeights(original=0.4, rerank=0.6),
    # Factual → cross-encoder good at fact matching
    "factual": RerankerWeights(original=0.2, rerank=0.8),
    # Exploratory → scoring pipeline already boosted recency, preserve that
    "exploratory": RerankerWeights(original=0.5, rerank=0.5),
    # Cross-domain → slightly favor reranker for broader matching
    "cross_domain": RerankerWeights(original=0.35, rerank=0.65),
}


def reranker_weights_for_intent(intent: str) -> RerankerWeights:
    """Return intent-tuned reranker weights, falling back to default."""
    return INTENT_RERANKER_WEIGHTS.get(intent, RerankerWeights())


@dataclass
class RerankerConfig:
    enabled: bool = True
    max_candidates: int = 20  # Only rerank top N
    snippet_length: int = 500  # Truncate content to this length
    weight_original: float = 0.3  # Weight of original score
    weight_rerank: float = 0.7  # Weight of cross-encoder score
    # Circuit breaker
    failure_threshold: int = 3
    recovery_seconds: float = 600  # 10 minutes
    # Attention-gated computation: skip reranker when scoring pipeline
    # output indicates high confidence (cheap signal gates expensive work)
    gate_enabled: bool = True
    gate_min_top_score: float = 0.55  # top-1 must exceed this (conservative: prefer rerank)
    gate_min_score_gap: float = 0.25  # gap between #1 and #2 (wide gap = clear winner)
    gate_max_candidates: int = 2  # skip only when ≤2 survive (1 result = nothing to rerank)
    gate_min_cluster_tightness: float = 0.03  # top-5 std_dev below this = skip (very tight)


class CircuitBreaker:
    """Simple circuit breaker for reranker failures."""

    def __init__(self, threshold: int = 3, recovery: float = 600):
        self.threshold = threshold
        self.recovery = recovery
        self.failures = 0
        self.last_failure: float = 0
        self.open = False

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.threshold:
            self.open = True

    def record_success(self):
        self.failures = 0
        self.open = False

    def is_available(self) -> bool:
        if not self.open:
            return True
        # Check if recovery period has passed
        if time.time() - self.last_failure > self.recovery:
            self.open = False
            self.failures = 0
            return True
        return False


class LocalReranker:
    """Rerank search results using Jina cross-encoder via MLX bridge."""

    def __init__(self, config: RerankerConfig | None = None):
        self.config = config or RerankerConfig()
        self._breaker = CircuitBreaker(
            self.config.failure_threshold,
            self.config.recovery_seconds,
        )

    def _should_gate(self, results: list[dict]) -> tuple[bool, str]:
        """Determine if reranker can be safely skipped.

        Uses scoring pipeline output as cheap pre-computed signal.
        Returns (should_skip, reason).
        """
        if _GATE_FORCE_DISABLED or not self.config.gate_enabled:
            return False, ""

        n = len(results)

        # Gate 1: Very few candidates — scoring pipeline already filtered heavily
        if n <= self.config.gate_max_candidates:
            return True, f"few_candidates({n})"

        scores = [r.get("score", 0.0) for r in results]
        top_score = scores[0]  # results are pre-sorted descending

        # Gate 2: Top score dominance — clear winner
        if n >= 2:
            gap = top_score - scores[1]
            if (
                top_score >= self.config.gate_min_top_score
                and gap >= self.config.gate_min_score_gap
            ):
                return True, f"score_dominance(top={top_score:.3f},gap={gap:.3f})"

        # Gate 3: Tight cluster — all top candidates similarly scored
        top_k = scores[: min(5, n)]
        if len(top_k) >= 3:
            mean = sum(top_k) / len(top_k)
            variance = sum((s - mean) ** 2 for s in top_k) / len(top_k)
            std_dev = variance**0.5
            if (
                std_dev <= self.config.gate_min_cluster_tightness
                and mean >= self.config.gate_min_top_score
            ):
                return True, f"tight_cluster(std={std_dev:.4f},mean={mean:.3f})"

        return False, ""

    async def rerank(
        self,
        query: str,
        results: list[dict],
        intent: str = "unknown",
    ) -> tuple[list[dict], bool, str | None]:
        """Rerank results using Jina cross-encoder.

        Returns (reranked_results, was_applied, gate_reason).
        gate_reason is set when the reranker was skipped by attention-gated computation.
        """
        if not self.config.enabled or not self._breaker.is_available():
            return results, False, None

        if len(results) <= 1:
            return results, False, None

        # Attention-gated computation: skip reranker if scoring pipeline
        # output indicates high confidence
        gated, gate_reason = self._should_gate(results)
        if gated:
            logger.debug(
                "reranker.gated",
                extra={"reason": gate_reason, "n_candidates": len(results)},
            )
            return results, False, gate_reason

        # Intent-dependent blend weights (AttnRes-inspired)
        weights = reranker_weights_for_intent(intent)

        try:
            from src.shared import rerank_bridge

            # Limit candidates
            candidates = results[: self.config.max_candidates]
            remainder = results[self.config.max_candidates :]

            # Prepare document snippets for cross-encoder
            snippets = [r.get("content", "")[: self.config.snippet_length] for r in candidates]

            # Call cross-encoder
            scores = await rerank_bridge.rerank(query, snippets)
            if scores is None:
                self._breaker.record_failure()
                return results, False, None

            # Build index→score map
            score_map = {s["index"]: s["score"] for s in scores}

            if len(scores) != len(candidates):
                logger.warning(
                    "Rerank score count mismatch: sent %d, got %d",
                    len(candidates),
                    len(scores),
                )

            # Blend original score with cross-encoder score
            for i, r in enumerate(candidates):
                ce_score = score_map.get(i)
                if ce_score is not None:
                    # Normalize cross-encoder score from [-1, 1] to [0, 1]
                    ce_normalized = (ce_score + 1) / 2
                    r["score"] = (
                        weights.original * r["score"]
                        + weights.rerank * ce_normalized
                    )

            # Re-sort candidates by new score
            candidates.sort(key=lambda r: r["score"], reverse=True)

            self._breaker.record_success()
            return candidates + remainder, True, None

        except Exception:
            logger.exception("Reranking failed")
            self._breaker.record_failure()
            return results, False, None


# Module singleton
_reranker = LocalReranker()


async def rerank_results(
    query: str, results: list[dict], intent: str = "unknown",
) -> tuple[list[dict], bool, str | None]:
    """Convenience function using module singleton."""
    return await _reranker.rerank(query, results, intent=intent)
