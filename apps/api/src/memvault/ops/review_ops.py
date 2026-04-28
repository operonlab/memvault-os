"""Memvault Review Operator — auto-approve stale pending items.

Runs as the final stage in the Dream Pipeline, cleaning up pending review
items that have not been manually reviewed within the configured window.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ._base import MemvaultOp

logger = logging.getLogger(__name__)


class ReviewAutoApproveOp(MemvaultOp):
    """Auto-approve __pending__ blocks older than review_auto_approve_days."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id", "dry_run")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("review_auto_approved_count",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..models import MemoryBlock

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        dry_run: bool = ctx.get("dry_run", False)
        days = self._config.review_auto_approve_days

        cutoff = datetime.now(UTC) - timedelta(days=days)

        if dry_run:
            # Count only
            from sqlalchemy import func, select

            stmt = (
                select(func.count())
                .select_from(MemoryBlock)
                .where(
                    MemoryBlock.space_id == space_id,
                    MemoryBlock.deleted_at.is_(None),
                    MemoryBlock.superseded_by == "__pending__",
                    MemoryBlock.updated_at < cutoff,
                )
            )
            result = await db.execute(stmt)
            count = result.scalar() or 0
        else:
            # Auto-approve: change __pending__ to __auto_approved__
            stmt = (
                update(MemoryBlock)
                .where(
                    MemoryBlock.space_id == space_id,
                    MemoryBlock.deleted_at.is_(None),
                    MemoryBlock.superseded_by == "__pending__",
                    MemoryBlock.updated_at < cutoff,
                )
                .values(
                    superseded_by="__auto_approved__",
                    invalidation_reason="review_auto_approve",
                    invalid_at=datetime.now(UTC),
                )
            )
            result = await db.execute(stmt)
            count = result.rowcount
            if count > 0:
                await db.flush()

        ctx["review_auto_approved_count"] = count
        logger.info(
            "Review auto-approve: %d items %s",
            count,
            "(dry-run)" if dry_run else "approved",
        )
        return ctx
