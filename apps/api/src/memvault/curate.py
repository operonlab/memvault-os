"""Memvault Curate — automated knowledge quality maintenance.

Removes low-confidence, never-accessed, old memory blocks (soft-delete).
Cleans up orphaned KG triples that are already invalidated and aged out.

Safety constraints (three-guard):
1. confidence < threshold  AND
2. access_count == 0       AND
3. age > MIN_BLOCK_AGE_DAYS

These three guards together ensure we never remove recently-created or
ever-accessed blocks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# --- Constants ---
MAX_SOFT_DELETE_PER_RUN = 50
MIN_BLOCK_AGE_DAYS = 30
MIN_ACCESS_FOR_PROTECTION = 1  # blocks with >= 1 access are protected
CURATE_COOLDOWN_HOURS = 24


async def curate_space(
    db: AsyncSession,
    space_id: str,
    confidence_threshold: float = 0.15,
    dry_run: bool = False,
) -> dict:
    """Curate knowledge quality for a space.

    Phase 1 operations:
    1. Soft-delete low-confidence blocks (confidence < threshold AND
       access_count == 0 AND age > MIN_BLOCK_AGE_DAYS).
    2. Hard-delete orphan triples (invalid_at IS NOT NULL AND age > 90d).

    Args:
        db: Async database session.
        space_id: Space to curate.
        confidence_threshold: Confidence below which blocks are candidates
            for removal (default 0.15).
        dry_run: If True, count candidates without making changes.

    Returns:
        dict with blocks_soft_deleted, triples_invalidated,
        orphan_triples_cleaned, dry_run.
    """
    from sqlalchemy import delete, func, select

    from .kg_models import Triple
    from .models import MemoryBlock

    blocks_soft_deleted = 0
    orphan_triples_cleaned = 0
    age_cutoff = datetime.now(UTC) - timedelta(days=MIN_BLOCK_AGE_DAYS)
    orphan_cutoff = datetime.now(UTC) - timedelta(days=90)

    # --- 1. Low-confidence block soft-delete ---
    try:
        candidate_q = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                MemoryBlock.deleted_at.is_(None),
                MemoryBlock.confidence.isnot(None),
                MemoryBlock.confidence < confidence_threshold,
                MemoryBlock.access_count < MIN_ACCESS_FOR_PROTECTION,
                MemoryBlock.created_at < age_cutoff,
            )
            .limit(MAX_SOFT_DELETE_PER_RUN)
        )

        if dry_run:
            count_q = select(func.count()).select_from(candidate_q.subquery())
            blocks_soft_deleted = (await db.execute(count_q)).scalar() or 0
        else:
            candidates = (await db.execute(candidate_q)).scalars().all()
            now = datetime.now(UTC)
            for block in candidates:
                block.deleted_at = now
            blocks_soft_deleted = len(candidates)
            if candidates:
                await db.flush()
                logger.info(
                    "curate.blocks_soft_deleted count=%d space=%s threshold=%.2f",
                    blocks_soft_deleted,
                    space_id,
                    confidence_threshold,
                )
    except Exception:
        logger.warning(
            "curate.blocks_soft_delete_failed space=%s",
            space_id,
            exc_info=True,
        )

    # --- 2. Orphan triple hard-delete (already invalidated + aged out) ---
    try:
        orphan_q = select(Triple).where(
            Triple.space_id == space_id,
            Triple.invalid_at.isnot(None),
            Triple.invalid_at < orphan_cutoff,
        )

        if dry_run:
            count_q = select(func.count()).select_from(orphan_q.subquery())
            orphan_triples_cleaned = (await db.execute(count_q)).scalar() or 0
        else:
            orphan_ids_q = select(Triple.id).where(
                Triple.space_id == space_id,
                Triple.invalid_at.isnot(None),
                Triple.invalid_at < orphan_cutoff,
            )
            orphan_ids = (await db.execute(orphan_ids_q)).scalars().all()
            if orphan_ids:
                del_stmt = delete(Triple).where(Triple.id.in_(orphan_ids))
                result = await db.execute(del_stmt)
                orphan_triples_cleaned = result.rowcount or 0
                await db.flush()
                logger.info(
                    "curate.orphan_triples_cleaned count=%d space=%s",
                    orphan_triples_cleaned,
                    space_id,
                )
    except Exception:
        logger.warning(
            "curate.orphan_triple_cleanup_failed space=%s",
            space_id,
            exc_info=True,
        )

    return {
        "blocks_soft_deleted": blocks_soft_deleted,
        "triples_invalidated": 0,  # Phase 1: corrections stored as new triples, not invalidated
        "orphan_triples_cleaned": orphan_triples_cleaned,
        "dry_run": dry_run,
    }
