"""Dream Pipeline — Orient → body (Gather → Reflect → Consolidate → Prune → ReviewAutoApprove).

Usage:
    pipeline = build_dream_pipeline(config)
    ctx = await pipeline.execute({
        "db": db,
        "space_id": space_id,
        "dry_run": True,
        "force": False,
    })
    if ctx.get("should_proceed"):
        report = {
            "signal":      ctx["signal_stats"],
            "reflect":     ctx["reflect_result"],
            "consolidate": ctx["consolidate_stats"],
            "prune":       ctx["prune_stats"],
            "review":      ctx.get("review_auto_approved_count", 0),
        }
"""

from __future__ import annotations

from src.shared.reactive import ConditionalOp, Pipeline

from ..ops.dream_ops import (
    DreamConsolidateOp,
    DreamGatherSignalOp,
    DreamOrientOp,
    DreamPruneOp,
    DreamReflectOp,
)
from ..ops.review_ops import ReviewAutoApproveOp
from ..pipeline_config import MemvaultPipelineConfig


def build_dream_pipeline(
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the dream pipeline.

    Args:
        config: Pipeline config for stage toggles. Defaults to MemvaultPipelineConfig().

    Returns:
        Compiled Pipeline ready for execution.
        Initial ctx must contain: db, space_id, dry_run, force.
    """
    if config is None:
        config = MemvaultPipelineConfig()

    # Phases 2-4 + review auto-approve: the conditional body
    body = Pipeline(name="dream.body")
    body.pipe(
        DreamGatherSignalOp("dream.gather_signal", config),
        DreamReflectOp("dream.reflect", config),
        DreamConsolidateOp("dream.consolidate", config),
        DreamPruneOp("dream.prune", config),
        ReviewAutoApproveOp("review.auto_approve", config),
    )

    gate = ConditionalOp(
        predicate=lambda ctx: bool(ctx.get("should_proceed")),
        then_op=body,
        name="dream.gate",
        predicate_keys=("should_proceed",),
    )

    pipeline = Pipeline(name="dream_pipeline")
    pipeline.pipe(
        DreamOrientOp("dream.orient", config),
        gate,
    )

    missing = pipeline.compile(initial_keys={"db", "dry_run", "force", "space_id"})
    if missing:
        raise RuntimeError(f"dream_pipeline compile errors: {missing}")

    return pipeline
