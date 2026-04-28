"""Memvault Dream Operators — thin MemvaultOp wrappers over dream.py phase functions.

Each op delegates to the corresponding private phase function in ..dream.
No logic is duplicated here; ops are pure routing wrappers.

Pipeline shape (assembled in pipelines/dream_pipeline.py):
    DreamOrientOp
      → ConditionalOp(should_proceed)
          → DreamGatherSignalOp
          → DreamReflectOp
          → DreamConsolidateOp
          → DreamPruneOp
"""

from __future__ import annotations

from typing import Any

from ._base import MemvaultOp


class DreamOrientOp(MemvaultOp):
    """Phase 1 — stats snapshot + dual-gate trigger check."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "force", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("last_dream_at", "orient_stats", "should_proceed")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..dream import _orient

        stats, should_proceed = await _orient(ctx["db"], ctx["space_id"], ctx["force"])
        ctx["orient_stats"] = stats
        ctx["should_proceed"] = should_proceed
        ctx["last_dream_at"] = stats.get("last_dream_at")
        return ctx


class DreamGatherSignalOp(MemvaultOp):
    """Phase 2 — scan recent blocks + broad contradiction detection."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "last_dream_at", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("_findings", "signal_stats")

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..dream import _gather_signal

        signal = await _gather_signal(ctx["db"], ctx["space_id"], ctx["last_dream_at"])
        ctx["signal_stats"] = {k: v for k, v in signal.items() if not k.startswith("_")}
        ctx["_findings"] = signal.get("_findings", [])
        return ctx


class DreamReflectOp(MemvaultOp):
    """Phase 2.5 — LLM reflective pass over memory state."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "orient_stats", "signal_stats", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("reflect_result",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..dream import _reflect

        ctx["reflect_result"] = await _reflect(
            ctx["db"],
            ctx["space_id"],
            ctx["orient_stats"],
            ctx["signal_stats"],
        )
        return ctx


class DreamConsolidateOp(MemvaultOp):
    """Phase 3 — contradiction resolution + batch dedup + content normalisation."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("_findings", "db", "dry_run", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("consolidate_stats",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..dream import _consolidate

        # _consolidate expects signal dict with "_findings" key
        signal_dict = {"_findings": ctx["_findings"]}
        ctx["consolidate_stats"] = await _consolidate(
            ctx["db"],
            ctx["space_id"],
            signal_dict,
            ctx["dry_run"],
        )
        return ctx


class DreamPruneOp(MemvaultOp):
    """Phase 4 — curate low-confidence blocks + lint remediation."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "dry_run", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("prune_stats",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..dream import _prune_and_report

        ctx["prune_stats"] = await _prune_and_report(ctx["db"], ctx["space_id"], ctx["dry_run"])
        return ctx
