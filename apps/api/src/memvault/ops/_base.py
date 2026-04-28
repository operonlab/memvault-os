"""MemvaultOp — base class for all memvault Reactive Operators.

Follows the ScoringOp pattern (scoring_pipeline.py:137-181) with:
- Per-stage toggle via MemvaultPipelineConfig.is_enabled()
- Try/catch error isolation (stage recorded as skipped on failure)
- Timing instrumentation per stage
- Consistent PipelineMeta tracking

Implements the Operator protocol from core/src/shared/reactive.py:164-191.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from ..pipeline_config import MemvaultPipelineConfig

logger = logging.getLogger(__name__)


@dataclass
class PipelineMeta:
    """Pipeline execution metadata — tracks which stages ran, skipped, errored."""

    stages_applied: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)  # ms
    stage_errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_ms(self) -> float:
        return sum(self.stage_timings.values())


class MemvaultOp:
    """Base for all memvault Reactive Operators.

    Subclasses override ``execute(ctx)`` with their logic.
    The base ``__call__`` handles:
    1. Toggle check — skip if stage disabled in config
    2. Error isolation — catch exceptions, log, mark as skipped
    3. Timing — record elapsed ms per stage
    4. Metadata — populate PipelineMeta in ctx["_pipeline_meta"]
    """

    def __init__(self, stage_name: str, config: MemvaultPipelineConfig) -> None:
        self._stage_name = stage_name
        self._config = config

    @property
    def name(self) -> str:
        return self._stage_name

    @property
    def input_keys(self) -> tuple[str, ...]:
        raise NotImplementedError

    @property
    def output_keys(self) -> tuple[str, ...]:
        raise NotImplementedError

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        meta: PipelineMeta = ctx.setdefault("_pipeline_meta", PipelineMeta())

        # Toggle check
        if not self._config.is_enabled(self._stage_name):
            meta.stages_skipped.append(self._stage_name)
            return ctx

        # Execute with error isolation + timing
        t0 = time.monotonic()
        try:
            ctx = await self.execute(ctx)
            meta.stages_applied.append(self._stage_name)
        except Exception:
            logger.exception("MemvaultOp '%s' failed, skipping", self._stage_name)
            meta.stages_skipped.append(self._stage_name)
            meta.stage_errors[self._stage_name] = traceback.format_exc()
        meta.stage_timings[self._stage_name] = (time.monotonic() - t0) * 1000
        return ctx

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Subclass override point. Raise on failure; base handles isolation."""
        raise NotImplementedError

    def __repr__(self) -> str:
        enabled = self._config.is_enabled(self._stage_name)
        return f"{self.__class__.__name__}({self._stage_name}, enabled={enabled})"
