"""Memvault Lint Operators — thin MemvaultOp wrappers over lint.py check functions.

Each op calls one check function from ..lint and writes findings to a unique
findings_{check_name} key so ParallelOp merges don't collide.

All ops require ("db", "space_id") in ctx.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from ._base import MemvaultOp, PipelineMeta

logger = logging.getLogger(__name__)


class LintOp(MemvaultOp):
    """Base for lint check operators.

    Overrides MemvaultOp.__call__ so that failures produce a LintFinding
    with severity="error" instead of silently swallowing the exception.
    This matches the behaviour of the original sequential run_lint() path.
    """

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        meta: PipelineMeta = ctx.setdefault("_pipeline_meta", PipelineMeta())

        if not self._config.is_enabled(self._stage_name):
            meta.stages_skipped.append(self._stage_name)
            return ctx

        t0 = time.monotonic()
        try:
            ctx = await self.execute(ctx)
            meta.stages_applied.append(self._stage_name)
        except Exception as exc:
            logger.exception("LintOp '%s' failed", self._stage_name)
            meta.stages_skipped.append(self._stage_name)
            meta.stage_errors[self._stage_name] = traceback.format_exc()

            # Produce an error LintFinding so the report is never falsely clean
            from ..lint import LintFinding

            check_name = self._stage_name.removeprefix("lint.")
            error_finding = LintFinding(
                check=check_name,
                severity="error",
                entity_id="",
                entity_type="system",
                message=f"Check '{check_name}' failed: {exc}",
                suggested_action="none",
            )
            findings_key = f"findings_{check_name}"
            ctx.setdefault(findings_key, []).append(error_finding)

        meta.stage_timings[self._stage_name] = (time.monotonic() - t0) * 1000
        return ctx


class LintContradictionOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_contradictions",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_contradictions

        results = await check_contradictions(
            ctx["db"],
            ctx["space_id"],
            sample_size=self._config.lint_contradiction_sample_size,
        )
        ctx["findings_contradictions"] = results
        return ctx


class LintStaleOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_stale",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_stale_triples

        results = await check_stale_triples(
            ctx["db"],
            ctx["space_id"],
            days_threshold=self._config.lint_stale_days,
        )
        ctx["findings_stale"] = results
        return ctx


class LintOrphanOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_orphan_entities",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_orphan_entities

        results = await check_orphan_entities(ctx["db"], ctx["space_id"])
        ctx["findings_orphan_entities"] = results
        return ctx


class LintDanglingRefOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_dangling_refs",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_dangling_refs

        results = await check_dangling_refs(ctx["db"], ctx["space_id"])
        ctx["findings_dangling_refs"] = results
        return ctx


class LintCommunityAnomalyOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_community_anomalies",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_community_anomalies

        results = await check_community_anomalies(ctx["db"], ctx["space_id"])
        ctx["findings_community_anomalies"] = results
        return ctx


class LintDataGapOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_data_gaps",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_data_gaps

        results = await check_data_gaps(ctx["db"], ctx["space_id"])
        ctx["findings_data_gaps"] = results
        return ctx


class LintPredicateContradictionOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_predicate_contradictions",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_predicate_contradictions

        results = await check_predicate_contradictions(ctx["db"], ctx["space_id"])
        ctx["findings_predicate_contradictions"] = results
        return ctx


class LintTemporalStalenessOp(LintOp):
    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings_temporal_staleness",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..lint import check_temporal_staleness

        results = await check_temporal_staleness(ctx["db"], ctx["space_id"])
        ctx["findings_temporal_staleness"] = results
        return ctx


# ── Merge stage ──────────────────────────────────────────────────────────────

_FINDINGS_KEYS = (
    "findings_contradictions",
    "findings_stale",
    "findings_orphan_entities",
    "findings_dangling_refs",
    "findings_community_anomalies",
    "findings_data_gaps",
    "findings_predicate_contradictions",
    "findings_temporal_staleness",
)


class MergeFindingsOp(MemvaultOp):
    """Collect all findings_* keys present in ctx into a single 'findings' list.

    input_keys is empty so compile() never rejects partial check runs.
    The op dynamically scans for any key matching the findings_* prefix.
    """

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ()  # no hard dependency — partial runs are valid

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("findings",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        merged: list = []
        for key in list(ctx):
            if key.startswith("findings_"):
                merged.extend(ctx.get(key) or [])
        ctx["findings"] = merged
        return ctx
