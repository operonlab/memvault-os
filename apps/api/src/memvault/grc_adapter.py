"""Memvault G-R-C Adapter.

Implements SupportsReflect + SupportsCurate + SupportsGenerate protocols from
src.shared.grc for the memvault module.

Registered as standard GRC routes via create_grc_routes() in __init__.py.
Provides /reflect and /curate endpoints with GRC standard response format.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.grc import (
    CurateAction,
    CurateResult,
    GenerateItem,
    GRCConfig,
    ReflectResult,
    classify_content,
    extract_key_sentence,
    three_guard_filter,
)

from .kg_config import PROTECTED_BLOCK_TYPES

logger = logging.getLogger(__name__)

# How far back fetch_blocks looks (days)
_FETCH_BLOCKS_DAYS = 30


class MemvaultGRCAdapter:
    """Memvault G-R-C adapter — implements SupportsReflect + SupportsCurate + SupportsGenerate.

    Registered via create_grc_routes() in __init__.py. Provides standard GRC
    /reflect and /curate endpoints for cross-module orchestration and runners.
    """

    module = "memvault"

    # ======================== Pre-fetch ========================

    async def fetch_blocks(self, db: AsyncSession, scope_id: str) -> list[dict]:
        """Pre-fetch recent MemoryBlocks for GRC analysis.

        Called by grc_routes reflect/curate endpoints before gather_items().
        Returns dicts with fields needed by gather_items() and identify_candidates().
        """
        from .models import MemoryBlock

        cutoff = datetime.now(UTC) - timedelta(days=_FETCH_BLOCKS_DAYS)
        q = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == scope_id,
                MemoryBlock.deleted_at.is_(None),
                MemoryBlock.created_at >= cutoff,
            )
            .order_by(MemoryBlock.created_at.desc())
        )
        rows = (await db.execute(q)).scalars().all()
        return [
            {
                "id": str(r.id),
                "content": r.content,
                "block_type": r.block_type,
                "tags": r.tags or [],
                "confidence": r.confidence,
                "access_count": r.access_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    # ======================== SupportsGenerate ========================

    def gather_items(self, scope_id: str, **kwargs: Any) -> list[GenerateItem]:
        """Convert pre-fetched MemoryBlock dicts into GenerateItems.

        Args:
            scope_id: space_id or session_id (caller's choice).
            kwargs:
                blocks: list[dict] — pre-fetched MemoryBlock dicts from the route/service layer.
                    Each dict should have: id, content, block_type, tags, confidence,
                    access_count, created_at.

        Note: Sync by design. The route layer should pre-fetch blocks from the DB
        and pass them in via kwargs['blocks'] to keep this adapter DB-agnostic.
        """
        blocks = kwargs.get("blocks", [])
        return [
            GenerateItem(
                id=b.get("id", ""),
                content=b.get("content", ""),
                metadata={
                    "block_type": b.get("block_type", ""),
                    "tags": b.get("tags") or [],
                    "confidence": b.get("confidence"),
                    "access_count": b.get("access_count", 0),
                    "created_at": b.get("created_at"),
                },
            )
            for b in blocks
        ]

    # ======================== SupportsReflect ========================

    def reflect(self, items: list[GenerateItem], scope_id: str) -> ReflectResult:
        """Extract invariants, corrections, and derived insights from items.

        Uses shared classify_content() + extract_key_sentence() pure functions.
        Deduplicates by key-word fingerprint (same logic as reflect_on_session()).

        Mapping to ReflectResult fields:
          invariant  → insights
          correction → corrections
          derived    → anomalies (reused as derived insights store)
        """
        result = ReflectResult(
            module=self.module,
            scope_id=scope_id,
            items_analyzed=len(items),
        )

        seen: set[frozenset[str]] = set()

        for item in items:
            if not item.content or len(item.content) < 20:
                continue

            category = classify_content(item.content)
            if not category:
                continue

            key = extract_key_sentence(item.content)
            key_words = frozenset(key.lower().split()[:10])
            if key_words in seen:
                continue
            seen.add(key_words)

            if category == "invariant":
                result.insights.append(key)
            elif category == "correction":
                result.corrections.append(key)
            else:  # derived
                result.anomalies.append(key)

        # Cap at GRCConfig defaults
        cfg = GRCConfig()
        result.insights = result.insights[: cfg.max_insights]
        result.corrections = result.corrections[: cfg.max_corrections]
        result.anomalies = result.anomalies[: cfg.max_anomalies]

        return result

    def generate_derived(self, reflect_result: ReflectResult, **kwargs: Any) -> list[Any]:
        """Hook for KG write-back or downstream derived generation.

        Actual KG writes (triples_created, etc.) are handled by routes.py via
        apply_reflection_to_kg() — this adapter method intentionally returns an
        empty list to avoid double-writing.
        """
        return []

    # ======================== SupportsCurate ========================

    def identify_candidates(
        self,
        scope_id: str,
        config: GRCConfig | None = None,
        **kwargs: Any,
    ) -> list[CurateAction]:
        """Identify low-quality memory blocks as soft-delete candidates.

        Uses shared three_guard_filter() to apply the three-guard heuristic:
        - Guard 1: confidence below threshold
        - Guard 2: access_count below minimum
        - Guard 3: item age exceeds minimum

        Items with protected block_types (lesson, correction, decision, rule)
        are excluded — these carry long-term value even at low confidence.

        Args:
            scope_id: space_id.
            config: GRCConfig overrides (defaults used if None).
            kwargs:
                blocks: list[dict] — pre-fetched MemoryBlock dicts.
        """
        blocks = kwargs.get("blocks", [])
        items = self.gather_items(scope_id, blocks=blocks)
        cfg = config or GRCConfig()
        candidates = three_guard_filter(items, cfg)

        return [
            CurateAction(
                item_id=item.id,
                action="soft_delete",
                reason=(
                    f"confidence={item.metadata.get('confidence') or 0:.2f}, "
                    f"access={item.metadata.get('access_count', 0)}, "
                    f"age>{cfg.min_item_age_days}d"
                ),
                confidence=1.0 - (item.metadata.get("confidence") or 0.0),
            )
            for item in candidates
            if item.metadata.get("block_type") not in PROTECTED_BLOCK_TYPES
        ]

    async def apply_actions(
        self,
        actions: list[CurateAction],
        dry_run: bool = False,
        **kwargs: Any,
    ) -> CurateResult:
        """Execute curation actions by delegating to memvault services.

        Performs soft-deletes for identified candidates via MemoryBlock.deleted_at.

        Args:
            actions: list of CurateAction from identify_candidates().
            dry_run: if True, no DB writes performed.
            kwargs:
                db: AsyncSession — required for writes.
                space_id: str — space scope (default: "default").
        """
        space_id = kwargs.get("space_id", "default")
        result = CurateResult(
            module=self.module,
            scope_id=space_id,
            dry_run=dry_run,
        )

        if not actions:
            return result

        db = kwargs.get("db")
        if not db:
            logger.warning("MemvaultGRCAdapter.apply_actions: db not provided, skipping writes")
            result.skipped_count = len(actions)
            return result

        if dry_run:
            result.skipped_count = len(actions)
            result.details = [
                {"item_id": a.item_id, "action": a.action, "reason": a.reason} for a in actions
            ]
            return result

        from sqlalchemy import update

        from .models import MemoryBlock

        soft_delete_ids = [a.item_id for a in actions if a.action == "soft_delete"]

        if soft_delete_ids:
            now = datetime.now(UTC)
            stmt = (
                update(MemoryBlock)
                .where(
                    MemoryBlock.id.in_(soft_delete_ids),
                    MemoryBlock.space_id == space_id,
                    MemoryBlock.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
            try:
                res = await db.execute(stmt)
                result.applied_count = res.rowcount
            except Exception:
                logger.exception("MemvaultGRCAdapter.apply_actions: soft-delete failed")
                result.error_count = len(soft_delete_ids)

        other_actions = [a for a in actions if a.action != "soft_delete"]
        result.skipped_count = len(other_actions)

        return result
