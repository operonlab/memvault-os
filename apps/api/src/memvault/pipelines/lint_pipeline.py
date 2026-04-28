"""Lint Pipeline factory — concurrent L0 checks + merge.

Usage:
    pipeline = build_lint_pipeline(checks, config)
    ctx = await pipeline.execute({"db": db, "space_id": space_id})
    findings = ctx["findings"]
"""

from __future__ import annotations

from src.shared.reactive import ParallelOp, Pipeline

from ..ops.lint_ops import (
    LintCommunityAnomalyOp,
    LintContradictionOp,
    LintDanglingRefOp,
    LintDataGapOp,
    LintOrphanOp,
    LintPredicateContradictionOp,
    LintStaleOp,
    LintTemporalStalenessOp,
    MergeFindingsOp,
)
from ..pipeline_config import MemvaultPipelineConfig

# Ordered mapping: check name → (Op class, stage_name in config)
_CHECK_REGISTRY: list[tuple[str, type, str]] = [
    ("contradictions", LintContradictionOp, "lint.contradictions"),
    ("stale", LintStaleOp, "lint.stale"),
    ("orphan_entities", LintOrphanOp, "lint.orphan_entities"),
    ("dangling_refs", LintDanglingRefOp, "lint.dangling_refs"),
    ("community_anomalies", LintCommunityAnomalyOp, "lint.community_anomalies"),
    ("data_gaps", LintDataGapOp, "lint.data_gaps"),
    ("predicate_contradictions", LintPredicateContradictionOp, "lint.predicate_contradictions"),
    ("temporal_staleness", LintTemporalStalenessOp, "lint.temporal_staleness"),
]


def build_lint_pipeline(
    checks: list[str] | None = None,
    config: MemvaultPipelineConfig | None = None,
) -> Pipeline:
    """Build the lint pipeline.

    Args:
        checks: Which check names to include. None means all registered checks.
        config: Pipeline config for stage toggles and parameters.

    Returns:
        Compiled Pipeline ready for execution.
    """
    if config is None:
        config = MemvaultPipelineConfig()

    selected_names = set(checks) if checks is not None else None

    ops = []
    for check_name, op_cls, stage_name in _CHECK_REGISTRY:
        if selected_names is not None and check_name not in selected_names:
            continue
        ops.append(op_cls(stage_name, config))

    if not ops:
        # No checks selected — return pipeline that just produces empty findings
        pipeline = Pipeline(name="lint_pipeline")
        merge = MergeFindingsOp("lint.merge", config)
        pipeline.pipe(merge)
        return pipeline

    merge = MergeFindingsOp("lint.merge", config)
    pipeline = Pipeline(name="lint_pipeline")

    if len(ops) >= 2:
        pipeline.pipe(ParallelOp(*ops, name="lint.parallel"), merge)
    else:
        # Single check — no ParallelOp needed (requires ≥2 ops)
        pipeline.pipe(ops[0], merge)

    missing = pipeline.compile(initial_keys={"db", "space_id"})
    if missing:
        raise RuntimeError(f"lint_pipeline compile errors: {missing}")

    return pipeline
