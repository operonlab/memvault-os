"""Query Pipeline factories — pre-search and post-search query transform stages.

Pre-search pipeline:  QueryRouteOp → QueryExpandOp
Post-search pipeline: RerankOp    → CRAGEvalOp

Search (CascadeRecallService) runs between the two pipelines and is NOT wrapped
as an Op — it remains an inline call in query_runtime.py.

Usage::

    pre  = build_query_pre_pipeline(config)
    post = build_query_post_pipeline(config)

    # Pre-search
    ctx = await pre.execute({"query": "..."})
    layer_plan  = ctx["layer_plan"]
    expanded_q  = ctx["expanded_query"]

    # ... inline CascadeRecallService.recall() ...

    # Post-search
    ctx["results"] = cascade_result  # CascadeRecallResult
    ctx["intent"]  = layer_plan.intent
    ctx = await post.execute(ctx)
    verdict    = ctx["verdict"]
    conf_score = ctx["confidence_score"]
"""

from __future__ import annotations

from src.shared.reactive import Pipeline

from ..ops.query_ops import CRAGEvalOp, QueryExpandOp, QueryRouteOp, RerankOp
from ..pipeline_config import MemvaultPipelineConfig


def build_query_pre_pipeline(
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the pre-search query pipeline.

    Stages: QueryRouteOp → QueryExpandOp

    Args:
        config: Pipeline config for stage toggles. Defaults to MemvaultPipelineConfig().

    Returns:
        Compiled Pipeline ready for execution.
        Initial ctx must contain: query (str).
    """
    if config is None:
        config = MemvaultPipelineConfig()

    pipeline = Pipeline(name="query_pre_pipeline")
    pipeline.pipe(
        QueryRouteOp("query.route", config),
        QueryExpandOp("query.expand", config),
    )

    missing = pipeline.compile(initial_keys={"query"})
    if missing:
        raise RuntimeError(f"query_pre_pipeline compile errors: {missing}")

    return pipeline


def build_query_post_pipeline(
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the post-search query pipeline.

    Stages: RerankOp → CRAGEvalOp

    Args:
        config: Pipeline config for stage toggles. Defaults to MemvaultPipelineConfig().

    Returns:
        Compiled Pipeline ready for execution.
        Initial ctx must contain: query (str), results (CascadeRecallResult | list[dict]),
        intent (QueryIntent | str).
    """
    if config is None:
        config = MemvaultPipelineConfig()

    pipeline = Pipeline(name="query_post_pipeline")
    pipeline.pipe(
        RerankOp("query.rerank", config),
        CRAGEvalOp("query.crag_eval", config),
    )

    missing = pipeline.compile(initial_keys={"intent", "query", "results"})
    if missing:
        raise RuntimeError(f"query_post_pipeline compile errors: {missing}")

    return pipeline
