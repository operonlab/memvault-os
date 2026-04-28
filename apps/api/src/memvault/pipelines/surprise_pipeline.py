"""Surprise Discovery Pipeline factory — find unexpected knowledge connections.

Usage:
    pipeline = build_surprise_pipeline(config)
    ctx = await pipeline.execute({"db": db, "space_id": space_id})
    surprises = ctx["surprises"]  # list[dict]

All three strategies run in parallel via ParallelOp.
"""

from __future__ import annotations

from src.shared.reactive import ParallelOp, Pipeline

from ..ops.surprise_ops import (
    MergeSurprisesOp,
    SurpriseCrossCommunityOp,
    SurpriseIndirectStrongOp,
    SurpriseKnowledgeGapOp,
)
from ..pipeline_config import MemvaultPipelineConfig


def build_surprise_pipeline(
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the surprise discovery pipeline.

    Args:
        config: Pipeline config for stage toggles and parameters.

    Returns:
        Compiled Pipeline ready for execution.
    """
    if config is None:
        config = MemvaultPipelineConfig()

    pipeline = Pipeline(name="surprise_pipeline")
    pipeline.pipe(
        ParallelOp(
            SurpriseIndirectStrongOp("surprise.indirect_strong", config),
            SurpriseCrossCommunityOp("surprise.cross_community", config),
            SurpriseKnowledgeGapOp("surprise.knowledge_gap", config),
            name="surprise.strategies",
        ),
        MergeSurprisesOp("surprise.merge", config),
    )

    missing = pipeline.compile(initial_keys={"db", "space_id"})
    if missing:
        raise RuntimeError(f"surprise_pipeline compile errors: {missing}")

    return pipeline
