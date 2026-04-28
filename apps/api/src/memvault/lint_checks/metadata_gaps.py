"""Check 6: metadata_gaps — required block columns missing.

Required fields per MemoryBlock model contract:
   - block_type        (defaulted server-side, but flag if blank string)
   - content           (Text, must not be NULL)
   - space_id          (must be set)
   - created_at        (timestamp; defaulted, flag if NULL)

We also flag suspiciously short tag arrays for `knowledge` blocks since the
schema says these blocks should at least have one tag (heuristic).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MemoryBlock

REQUIRED_FIELDS = ("block_type", "content", "space_id", "created_at")


async def check_metadata_gaps(
    db: AsyncSession,
    space_id: str,
    *,
    flag_missing_tags_for_types: tuple[str, ...] = ("knowledge",),
) -> list:
    from ..lint import LintFinding

    bq = select(MemoryBlock).where(
        MemoryBlock.space_id == space_id,
        MemoryBlock.deleted_at.is_(None),
        MemoryBlock.invalid_at.is_(None),
    )
    findings: list = []
    for b in (await db.execute(bq)).scalars().all():
        gaps: list[str] = []
        for field in REQUIRED_FIELDS:
            value = getattr(b, field, None)
            if value is None:
                gaps.append(field)
            elif isinstance(value, str) and not value.strip():
                gaps.append(field)

        # Heuristic: knowledge blocks should have at least one tag
        if (
            b.block_type in flag_missing_tags_for_types
            and not (b.tags or [])
        ):
            gaps.append("tags(empty)")

        if not gaps:
            continue
        findings.append(
            LintFinding(
                check="metadata_gaps",
                severity="warning",
                entity_id=b.id,
                entity_type="block",
                message=(
                    f"Block {b.id[:8]} missing required metadata: {', '.join(gaps)}"
                ),
                suggested_action=(
                    "Backfill the missing columns; for tag gaps, run the auto-tag "
                    "pipeline over this block."
                ),
                metadata={"block_id": b.id, "gaps": gaps},
            )
        )

    return findings
