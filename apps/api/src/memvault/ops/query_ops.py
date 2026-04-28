"""Memvault Query Operators — thin MemvaultOp wrappers over query-path functions.

Pre-search pipeline:
    QueryRouteOp → QueryExpandOp

Post-search pipeline:
    RerankOp → CRAGEvalOp

These two sub-pipelines are assembled in pipelines/query_pipeline.py.
Search itself (CascadeRecallService) runs between the two sub-pipelines and is
NOT wrapped as an Op — it remains an inline call in query_runtime.py.
"""

from __future__ import annotations

from typing import Any

from ._base import MemvaultOp


class QueryRouteOp(MemvaultOp):
    """Classify query intent and produce a LayerPlan (<1ms, regex-based)."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("query",)

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("intent", "layer_plan", "routing_confidence")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..query_router import classify_query_full

        layer_plan = await classify_query_full(ctx["query"])
        ctx["intent"] = layer_plan.intent
        ctx["layer_plan"] = layer_plan
        ctx["routing_confidence"] = layer_plan.confidence
        return ctx


class QueryExpandOp(MemvaultOp):
    """HyDE-style query expansion — produces expanded_text + keywords for retrieval."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("query",)

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("expanded_query", "keywords", "inferred_tags", "expansion_method")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..query_expander import expand_query

        expanded = await expand_query(ctx["query"])
        ctx["expanded_query"] = expanded
        ctx["keywords"] = expanded.keywords
        ctx["inferred_tags"] = expanded.inferred_tags
        ctx["expansion_method"] = expanded.expansion_used
        return ctx


class RerankOp(MemvaultOp):
    """Cross-encoder reranking with attention-gated computation (skip when redundant)."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("query", "results", "intent")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("results", "reranker_applied", "gate_reason")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..reranker import rerank_results

        reranked, applied, gate_reason = await rerank_results(
            ctx["query"],
            ctx["results"],
            intent=str(ctx["intent"]),
        )
        ctx["results"] = reranked
        ctx["reranker_applied"] = applied
        ctx["gate_reason"] = gate_reason
        return ctx


class CRAGEvalOp(MemvaultOp):
    """Corrective RAG evaluation — four-layer quality assessment of recall results."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("query", "results", "intent")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("verdict", "confidence_score", "evaluation_meta")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..crag_evaluator import CRAGEvaluator

        evaluator = CRAGEvaluator()
        evaluation = await evaluator.evaluate(
            ctx["query"],
            result=ctx["results"],
            intent=str(ctx["intent"]),
        )
        ctx["verdict"] = evaluation.verdict
        ctx["confidence_score"] = evaluation.confidence_score
        ctx["evaluation_meta"] = evaluation.metadata
        return ctx
