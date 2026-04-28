"""Multi-stage scoring pipeline for memvault search results.

Inspired by memory-lancedb-pro's 7-stage scoring system.
Each stage is independently bypassable and try-catch isolated.

G3 enhancement: Weibull decay replaces linear time decay for more
realistic memory forgetting curves with tier-aware parameters.

G6 enhancement: Access reinforcement — frequently-accessed memories
decay more slowly via compute_effective_half_life() from access_tracker.
Results dicts should carry access_count and last_accessed_at for this
stage to take effect (populated by services.py search queries).

Reactive Protocol: Each stage is a ScoringOp implementing the Operator protocol.
ScoringPipeline.apply() is async and uses Pipeline.execute() for composable
execution with compile() validation for key dependency chains.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.shared.access_tracker import compute_effective_half_life
from src.shared.decay import WEIBULL_PARAMS, weibull_decay, weibull_decay_with_half_life
from src.shared.reactive import Pipeline
from src.shared.scoring_stages import (
    apply_length_normalization,
    apply_min_score_filter,
    apply_recency_boost,
    cosine_similarity,
)
from text_ops.noise import check_noise

logger = logging.getLogger(__name__)

# --- Access reinforcement defaults (G6) ---
# Base half-life used when adjusting Weibull λ via access reinforcement.
# The effective λ replaces the tier's lambda_ when access_count > 0.
_ACCESS_BASE_HALF_LIFE_DAYS: float = 30.0


@dataclass
class ScoringConfig:
    recency_half_life: float = 14.0
    recency_weight: float = 0.15
    length_anchor: int = 500
    min_score: float = 0.10
    mmr_threshold: float = 0.85
    semantic_boost: float = 0.3
    trust_penalty: float = 0.3  # max penalty for low-trust memories
    feedback_weight: float = 0.15  # max boost/penalty from feedback signals
    stages_enabled: dict[str, bool] = field(
        default_factory=lambda: {
            "recency": True,
            "importance": True,
            "trust_boost": True,
            "feedback_boost": True,
            "length_norm": True,
            "time_decay": True,
            "semantic_boost": True,
            "min_score": True,
            "noise_filter": True,
            "mmr": True,
        }
    )


# ---------------------------------------------------------------------------
# Intent-Dependent Scoring Weights (AttnRes-inspired)
#
# Instead of fixed aggregation weights for all queries, each query intent
# gets tuned weights — analogous to AttnRes replacing fixed residual
# connections with content-dependent attention.
# ---------------------------------------------------------------------------

INTENT_SCORING_CONFIGS: dict[str, ScoringConfig] = {
    # "What is entity X?" — semantic similarity is king, recency less relevant
    "entity_lookup": ScoringConfig(
        recency_weight=0.05,
        semantic_boost=0.5,
        trust_penalty=0.2,
        feedback_weight=0.15,
    ),
    # "What do I think about X?" — deep semantic match, trust matters less
    "conceptual": ScoringConfig(
        recency_weight=0.05,
        semantic_boost=0.5,
        trust_penalty=0.1,
        feedback_weight=0.20,
    ),
    # "When did X happen?" — balanced, trust important for facts
    "factual": ScoringConfig(
        recency_weight=0.15,
        semantic_boost=0.3,
        trust_penalty=0.5,
        feedback_weight=0.10,
    ),
    # "What's been going on with X recently?" — recency is king
    "exploratory": ScoringConfig(
        recency_weight=0.35,
        semantic_boost=0.1,
        trust_penalty=0.2,
        feedback_weight=0.15,
    ),
    # Cross-domain queries — balanced with strong semantic
    "cross_domain": ScoringConfig(
        recency_weight=0.10,
        semantic_boost=0.4,
        trust_penalty=0.2,
        feedback_weight=0.15,
    ),
}


def scoring_config_for_intent(intent: str) -> ScoringConfig:
    """Return intent-tuned ScoringConfig, falling back to default."""
    return INTENT_SCORING_CONFIGS.get(intent, ScoringConfig())


@dataclass
class ScoringMetadata:
    stages_applied: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    noise_filtered: int = 0
    mmr_deduped: int = 0
    input_count: int = 0
    output_count: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Scoring Operators — each implements the Operator protocol from reactive.py
# ═══════════════════════════════════════════════════════════════════════════


class ScoringOp:
    """Base for scoring pipeline operators (Operator protocol).

    Wraps each stage with enable-check + try/except error isolation.
    Subclasses that filter results can set _count_key to auto-track
    before/after counts on ScoringMetadata (e.g., noise_filtered, mmr_deduped).
    """

    _count_key: str | None = None

    def __init__(self, stage_name: str, config: ScoringConfig) -> None:
        self._stage_name = stage_name
        self._config = config

    @property
    def name(self) -> str:
        return self._stage_name

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("results",)

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("results",)

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Async execution with enable-check + error isolation."""
        meta: ScoringMetadata = ctx["meta"]
        if not self._config.stages_enabled.get(self._stage_name, True):
            meta.stages_skipped.append(self._stage_name)
            return ctx
        try:
            before = len(ctx["results"]) if self._count_key else 0
            ctx["results"] = self.transform(ctx["results"], ctx)
            meta.stages_applied.append(self._stage_name)
            if self._count_key:
                setattr(meta, self._count_key, before - len(ctx["results"]))
        except Exception:
            logger.exception("Scoring stage '%s' failed, skipping", self._stage_name)
            meta.stages_skipped.append(self._stage_name)
        return ctx

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        raise NotImplementedError


class RecencyOp(ScoringOp):
    """Stage 1: Recency Boost — newer memories score higher."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("results", "now")

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        return apply_recency_boost(
            results,
            half_life_days=self._config.recency_half_life,
            weight=self._config.recency_weight,
            now=ctx["now"],
        )


class ImportanceOp(ScoringOp):
    """Stage 2: Importance Weight — confidence-based scoring."""

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        for r in results:
            confidence = r.get("confidence") or 0.5  # unset → neutral
            r["score"] *= 0.7 + 0.3 * confidence
        return results


class TrustBoostOp(ScoringOp):
    """Stage 2.5: Trust Boost — P3 source tracking → scoring integration."""

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        from .source_tracker import MemoryProvenance, compute_trust_score

        for r in results:
            block = r.get("block")
            if not block:
                continue
            session_id = getattr(block, "source_session", None)
            provenance = MemoryProvenance(
                source_session_id=session_id,
                extraction_method="auto_extract" if session_id else "manual",
            )
            trust = compute_trust_score(provenance)
            r["score"] *= 1.0 - self._config.trust_penalty * (1.0 - trust)
        return results


class FeedbackBoostOp(ScoringOp):
    """Stage 2.75: Feedback Boost — closed-loop learning from explicit feedback.

    Formula: score *= 1 + feedback_weight * tanh(net_signal / 3)
    """

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        for r in results:
            net = r.get("feedback_net") or 0
            if not net:
                continue
            r["score"] *= 1.0 + self._config.feedback_weight * math.tanh(net / 3.0)
        return results


class LengthNormOp(ScoringOp):
    """Stage 3: Length Normalization — penalize extreme content lengths."""

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        return apply_length_normalization(
            results,
            anchor_length=self._config.length_anchor,
        )


class TimeDecayOp(ScoringOp):
    """Stage 4: Time Decay — G3 Weibull + G6 access reinforcement."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("results", "now")

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        now = ctx["now"]
        for r in results:
            created_at = r.get("created_at")
            if not created_at:
                continue

            age_days = max((now - created_at).total_seconds() / 86400, 0)

            # Determine tier from confidence (G3 logic unchanged)
            confidence = r.get("confidence") or 0.5
            if confidence >= 0.8:
                tier = "core"
            elif confidence >= 0.5:
                tier = "hot"
            elif confidence >= 0.3:
                tier = "warm"
            else:
                tier = "cold"

            # G6: if access tracking data is present, compute effective half-life
            access_count: int = r.get("access_count") or 0
            last_accessed_at = r.get("last_accessed_at")

            if access_count > 0 and last_accessed_at is not None:
                tier_lambda = WEIBULL_PARAMS.get(tier, WEIBULL_PARAMS["hot"])["lambda_"]
                effective_hl = compute_effective_half_life(
                    access_count=access_count,
                    last_accessed_at=last_accessed_at,
                    created_at=created_at,
                    base_half_life_days=tier_lambda,
                )
                r["score"] *= weibull_decay_with_half_life(age_days, effective_hl, tier)
            else:
                r["score"] *= weibull_decay(age_days, tier)

        return results


class PPRBoostOp(ScoringOp):
    """Stage 4.3: PPR Centrality Boost — HippoRAG-inspired.

    Boosts results whose content mentions entities ranked highly by
    Personalized PageRank. Only active when PPR scores are available
    in the pipeline context (set by CascadeRecallService._ppr_recall).
    """

    PPR_WEIGHT = 0.3  # max boost factor

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        ppr_scores: dict[str, float] | None = ctx.get("ppr_scores")
        if not ppr_scores:
            return results

        for r in results:
            content = r.get("content", "").lower()
            # Check if content mentions any PPR-ranked entities
            max_ppr = 0.0
            for entity, score in ppr_scores.items():
                if entity.lower() in content:
                    max_ppr = max(max_ppr, score)
            if max_ppr > 0:
                r["score"] *= 1.0 + self.PPR_WEIGHT * max_ppr

        return results


class SemanticBoostOp(ScoringOp):
    """Stage 4.5: Semantic Relevance Boost — FadeMem-inspired."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("results", "query_embedding")

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        query_embedding = ctx.get("query_embedding")
        if not query_embedding:
            return results

        for r in results:
            emb = r.get("embedding")
            if not emb:
                continue
            similarity = cosine_similarity(emb, query_embedding)
            similarity = max(0.0, similarity)
            r["score"] *= 1.0 + self._config.semantic_boost * similarity

        return results


class MinScoreOp(ScoringOp):
    """Stage 5: Hard Min Score — remove results below threshold."""

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        return apply_min_score_filter(results, min_score=self._config.min_score)


class NoiseFilterOp(ScoringOp):
    """Stage 6: Noise Filter — remove greeting/noise content."""

    _count_key = "noise_filtered"

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        clean = []
        for r in results:
            content = r.get("content", "")
            verdict = check_noise(content)
            if not verdict.is_noise:
                clean.append(r)
        return clean


class PairwiseDedupOp(ScoringOp):
    """Stage 11: Pairwise Dedup — remove near-duplicates via cosine similarity."""

    _count_key = "mmr_deduped"

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("results", "query_embedding")

    def transform(self, results: list[dict], ctx: dict[str, Any]) -> list[dict]:
        query_embedding = ctx.get("query_embedding")
        if not query_embedding:
            return results

        to_remove = set()
        for i in range(len(results)):
            if i in to_remove:
                continue
            emb_i = results[i].get("embedding")
            if not emb_i:
                continue
            for j in range(i + 1, len(results)):
                if j in to_remove:
                    continue
                emb_j = results[j].get("embedding")
                if not emb_j:
                    continue
                sim = cosine_similarity(emb_i, emb_j)
                if sim > self._config.mmr_threshold:
                    results[j]["score"] *= 0.5
                    if results[j]["score"] < self._config.min_score:
                        to_remove.add(j)

        return [r for i, r in enumerate(results) if i not in to_remove]


# ═══════════════════════════════════════════════════════════════════════════
# ScoringPipeline — public API
# ═══════════════════════════════════════════════════════════════════════════


class ScoringPipeline:
    def __init__(self, config: ScoringConfig | None = None):
        self.config = config or ScoringConfig()
        self._pipeline = self._build_pipeline()

    def _build_pipeline(self) -> Pipeline:
        """Build the reactive Pipeline with all 11 scoring operators."""
        return Pipeline().pipe(
            RecencyOp("recency", self.config),
            ImportanceOp("importance", self.config),
            TrustBoostOp("trust_boost", self.config),
            FeedbackBoostOp("feedback_boost", self.config),
            LengthNormOp("length_norm", self.config),
            TimeDecayOp("time_decay", self.config),
            PPRBoostOp("ppr_boost", self.config),
            SemanticBoostOp("semantic_boost", self.config),
            MinScoreOp("min_score", self.config),
            NoiseFilterOp("noise_filter", self.config),
            PairwiseDedupOp("pairwise_dedup", self.config),
        )

    async def apply(
        self,
        results: list[dict],
        query_embedding: list[float] | None = None,
    ) -> tuple[list[dict], ScoringMetadata]:
        """Apply all enabled stages.

        Each result dict has: block, score, content, created_at, confidence, embedding (optional).
        G6 fields (optional): access_count, last_accessed_at — used by time_decay stage.
        """
        meta = ScoringMetadata(input_count=len(results))

        if not results:
            meta.output_count = 0
            return results, meta

        ctx: dict[str, Any] = {
            "results": results,
            "meta": meta,
            "now": datetime.now(UTC),
            "query_embedding": query_embedding,
        }

        ctx = await self._pipeline.execute(ctx)

        # Sort by final score descending
        results = ctx["results"]
        results.sort(key=lambda r: r["score"], reverse=True)
        meta.output_count = len(results)
        return results, meta
