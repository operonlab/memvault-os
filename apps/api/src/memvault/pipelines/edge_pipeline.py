"""Edge Weight Pipeline factory — compute multi-signal entity edge weights.

Usage:
    pipeline = build_edge_pipeline(config)
    ctx = await pipeline.execute({"db": db, "space_id": space_id})
    edges_upserted = ctx["edges_upserted"]

Dependency chain:
    S1(cooccurrence) → S3(adamic_adar)  ───┐
                                            ├→ Composite → Persist
    ParallelOp(S2, S4, S5) ────────────────┘
"""

from __future__ import annotations

from src.shared.reactive import ParallelOp, Pipeline

from ..ops.edge_ops import (
    EdgeAdamicAdarOp,
    EdgeCompositeOp,
    EdgeCooccurrenceOp,
    EdgePersistOp,
    EdgeSemanticSimilarityOp,
    EdgeSessionOverlapOp,
    EdgeTypeAffinityOp,
)
from ..pipeline_config import MemvaultPipelineConfig


def build_edge_pipeline(
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the edge weight computation pipeline.

    Args:
        config: Pipeline config for stage toggles and composite weights.

    Returns:
        Compiled Pipeline ready for execution.
    """
    if config is None:
        config = MemvaultPipelineConfig()

    pipeline = Pipeline(name="edge_pipeline")
    pipeline.pipe(
        # S1 must run first — S3 depends on its graph structure
        EdgeCooccurrenceOp("edge.cooccurrence", config),
        # S3 reads edge_cooccurrence_map → must be sequential after S1
        EdgeAdamicAdarOp("edge.adamic_adar", config),
        # S2, S4, S5 are independent → run in parallel
        ParallelOp(
            EdgeSessionOverlapOp("edge.session_overlap", config),
            EdgeTypeAffinityOp("edge.type_affinity", config),
            EdgeSemanticSimilarityOp("edge.semantic_similarity", config),
            name="edge.independent_signals",
        ),
        # Combine all signals
        EdgeCompositeOp("edge.composite", config),
        # Persist to DB
        EdgePersistOp("edge.persist", config),
    )

    missing = pipeline.compile(initial_keys={"db", "space_id"})
    if missing:
        raise RuntimeError(f"edge_pipeline compile errors: {missing}")

    return pipeline
