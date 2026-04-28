"""CRAG Evaluator — Corrective RAG result quality assessment.

Four-layer evaluation (fast → slow):
  Layer A (rule-based, <5ms):  always-on — result count, layer coverage, ILIKE-only penalty
  Layer B (cross-encoder, ~50ms): always-on — Jina Reranker v3 unified rerank
  Layer C (Haiku LLM, 1-3s):  opt-in via evaluate="deep"
  Layer D (RLM, 3-10s):       opt-in via evaluate="rlm"

Confidence thresholds:
  CORRECT:   avg_score >= 0.6 AND max_score >= 0.7
  AMBIGUOUS: 0.3 <= avg_score < 0.6
  INCORRECT: avg_score < 0.3 OR empty results
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from sdk_client.timeout import dynamic_timeout

from .llm_config import get_litellm_model
from .llm_models import CRAGVerdictOutput

if TYPE_CHECKING:
    from .kg_schemas import CascadeRecallResult

logger = logging.getLogger(__name__)

_haiku_eval_agent = Agent(
    output_type=CRAGVerdictOutput,
    system_prompt=(
        "You are evaluating search result relevance. Given a query and retrieved results, "
        "respond with your verdict."
    ),
    retries=2,
)


class CRAGVerdict(StrEnum):
    CORRECT = "correct"
    AMBIGUOUS = "ambiguous"
    INCORRECT = "incorrect"


@dataclass
class CRAGEvaluation:
    verdict: CRAGVerdict
    confidence_score: float
    metadata: dict = field(default_factory=dict)


@dataclass
class CRAGWeights:
    """Tunable weights for CRAG evaluation layers."""

    coverage: float = 0.4  # Layer A: coverage vs density
    density: float = 0.6
    rules: float = 0.3  # Layer A+B combination: rules vs rerank
    rerank: float = 0.7


# Intent-dependent CRAG weights (AttnRes-inspired)
INTENT_CRAG_WEIGHTS: dict[str, CRAGWeights] = {
    # Entity lookup → density matters (want specific hits)
    "entity_lookup": CRAGWeights(coverage=0.3, density=0.7, rules=0.2, rerank=0.8),
    # Conceptual → slightly favor coverage for breadth, feedback-weighted rerank
    "conceptual": CRAGWeights(coverage=0.45, density=0.55, rules=0.35, rerank=0.65),
    # Factual → reranker is most reliable for fact verification
    "factual": CRAGWeights(coverage=0.3, density=0.7, rules=0.2, rerank=0.8),
    # Exploratory → coverage matters (want breadth across layers)
    "exploratory": CRAGWeights(coverage=0.6, density=0.4, rules=0.4, rerank=0.6),
    # Cross-domain → coverage for breadth
    "cross_domain": CRAGWeights(coverage=0.5, density=0.5, rules=0.3, rerank=0.7),
}


def crag_weights_for_intent(intent: str) -> CRAGWeights:
    """Return intent-tuned CRAG weights, falling back to default."""
    return INTENT_CRAG_WEIGHTS.get(intent, CRAGWeights())


class CRAGEvaluator:
    """Multi-layer result quality evaluator."""

    async def evaluate(
        self,
        query: str,
        result: CascadeRecallResult,
        evaluate: str = "default",
        intent: str = "unknown",
    ) -> CRAGEvaluation:
        """Evaluate cascade recall results.

        Args:
            evaluate: "default" (Layer A+B), "deep" (+Haiku), "rlm" (+RLM), "none" (skip).
            intent: Query intent for weight tuning (AttnRes-inspired).
        """
        weights = crag_weights_for_intent(intent)

        # Layer A: Rule-based heuristics
        layer_a = self._layer_a_rules(result, weights)

        # Early exit if empty
        if layer_a["result_count"] == 0:
            return CRAGEvaluation(
                verdict=CRAGVerdict.INCORRECT,
                confidence_score=0.0,
                metadata={"layer_a": layer_a, "reason": "empty_results"},
            )

        # Layer B: Cross-encoder reranking
        layer_b = await self._layer_b_rerank(query, result)

        # Combine Layer A + B scores
        combined_score = self._combine_scores(layer_a, layer_b, weights)
        verdict = self._score_to_verdict(combined_score, layer_b)

        metadata: dict = {
            "layer_a": layer_a,
            "layer_b": layer_b,
            "combined_score": round(combined_score, 3),
        }

        # Layer C: Haiku LLM evaluation (opt-in)
        if evaluate == "deep" and verdict == CRAGVerdict.AMBIGUOUS:
            layer_c = await self._layer_c_haiku(query, result)
            if layer_c:
                metadata["layer_c"] = layer_c
                # Haiku verdict can override
                if layer_c.get("verdict"):
                    verdict = CRAGVerdict(layer_c["verdict"])
                    combined_score = layer_c.get("score", combined_score)

        # Layer D: RLM query decomposition (opt-in) — noted for future
        if evaluate == "rlm" and verdict == CRAGVerdict.INCORRECT:
            metadata["layer_d"] = {"status": "available", "note": "use expand_query_rlm for retry"}

        return CRAGEvaluation(
            verdict=verdict,
            confidence_score=round(combined_score, 3),
            metadata=metadata,
        )

    def _layer_a_rules(self, result: CascadeRecallResult, weights: CRAGWeights) -> dict:
        """Layer A: Rule-based heuristics (<5ms)."""
        layers = result.layers_searched
        n_summaries = len(result.summaries)
        n_communities = len(result.communities)
        n_triples = len(result.triples)
        n_blocks = len(result.blocks)
        total = n_summaries + n_communities + n_triples + n_blocks

        # Layer coverage score: more layers hit = higher confidence
        coverage = len(layers) / 4.0

        # Result density score
        density = min(total / 10.0, 1.0)  # cap at 10 results

        # Combine with intent-tuned weights
        score = coverage * weights.coverage + density * weights.density

        return {
            "result_count": total,
            "layers_hit": len(layers),
            "coverage_score": round(coverage, 3),
            "density_score": round(density, 3),
            "score": round(score, 3),
        }

    async def _layer_b_rerank(self, query: str, result: CascadeRecallResult) -> dict:
        """Layer B: Cross-encoder reranking (~50ms)."""
        try:
            from src.shared.rerank_bridge import rerank
        except ImportError:
            return {"status": "unavailable", "score": 0.5}

        # Collect all result texts for reranking
        documents: list[str] = []
        doc_sources: list[str] = []

        for s in result.summaries:
            text = s.summary
            if s.key_findings:
                text += " " + "; ".join(s.key_findings[:3])
            documents.append(text)
            doc_sources.append("summary")

        for c in result.communities:
            text = c.name
            if c.summary:
                text += " " + c.summary
            documents.append(text)
            doc_sources.append("community")

        for t in result.triples:
            documents.append(f"{t.subject} {t.predicate} {t.object}")
            doc_sources.append("triple")

        for b in result.blocks:
            content = b.get("content", "") if isinstance(b, dict) else getattr(b, "content", "")
            documents.append(content[:300])
            doc_sources.append("block")

        if not documents:
            return {"status": "no_documents", "score": 0.0}

        scores = await rerank(query, documents)
        if scores is None:
            return {"status": "worker_unavailable", "score": 0.5}

        if not scores:
            return {"status": "empty_scores", "score": 0.0}

        # Compute aggregate metrics
        score_values = [s["score"] for s in scores]
        avg_score = sum(score_values) / len(score_values) if score_values else 0.0
        max_score = max(score_values) if score_values else 0.0

        return {
            "status": "ok",
            "avg_score": round(avg_score, 3),
            "max_score": round(max_score, 3),
            "top_3": scores[:3],
            "score": round(avg_score, 3),
        }

    def _combine_scores(self, layer_a: dict, layer_b: dict, weights: CRAGWeights) -> float:
        """Combine Layer A (rules) and Layer B (cross-encoder) scores."""
        a_score = layer_a.get("score", 0.0)
        b_score = layer_b.get("score", 0.5)  # default 0.5 if reranker unavailable

        # Intent-tuned blend weights
        if layer_b.get("status") == "ok":
            return a_score * weights.rules + b_score * weights.rerank
        else:
            # No reranker — rely more on rules
            return a_score

    def _score_to_verdict(self, score: float, layer_b: dict) -> CRAGVerdict:
        """Map combined score to verdict."""
        # If cross-encoder is available, use its max_score as additional signal
        if layer_b.get("status") == "ok":
            max_score = layer_b.get("max_score", 0.0)
            avg_score = layer_b.get("avg_score", 0.0)

            if avg_score >= 0.6 and max_score >= 0.7:
                return CRAGVerdict.CORRECT
            if avg_score < 0.3:
                return CRAGVerdict.INCORRECT
            return CRAGVerdict.AMBIGUOUS

        # Fallback: rule-based only
        if score >= 0.6:
            return CRAGVerdict.CORRECT
        if score < 0.3:
            return CRAGVerdict.INCORRECT
        return CRAGVerdict.AMBIGUOUS

    async def _layer_c_haiku(self, query: str, result: CascadeRecallResult) -> dict | None:
        """Layer C: Haiku LLM evaluation (opt-in, 1-3s)."""
        try:
            # Build context from top results
            context_parts: list[str] = []
            for s in result.summaries[:2]:
                context_parts.append(f"[Summary] {s.summary}")
            for c in result.communities[:2]:
                context_parts.append(f"[Community] {c.name}: {c.summary or ''}")
            for t in result.triples[:3]:
                context_parts.append(f"[Triple] {t.subject} → {t.predicate} → {t.object}")
            for b in result.blocks[:2]:
                content = (
                    b.get("content", "")[:200]
                    if isinstance(b, dict)
                    else getattr(b, "content", "")[:200]
                )
                context_parts.append(f"[Block] {content}")

            context = "\n".join(context_parts)
            user_msg = f"Query: {query}\n\nResults:\n{context}"

            # Dynamic timeout: scale by context size, cap at 30s
            ctx_chars = len(context)
            timeout = dynamic_timeout(base=5, factor=0.5, context=ctx_chars / 1000, cap=30)

            ai_result = await _haiku_eval_agent.run(
                user_msg,
                model=await get_litellm_model(),
                model_settings={"temperature": 0.0, "max_tokens": 128, "timeout": timeout},
            )
            verdict = ai_result.output.verdict
            score_map = {"correct": 0.8, "ambiguous": 0.5, "incorrect": 0.1}
            return {"verdict": verdict, "score": score_map[verdict]}

        except Exception as e:
            logger.warning("Layer C (Haiku) evaluation failed: %s", e)
            return None
