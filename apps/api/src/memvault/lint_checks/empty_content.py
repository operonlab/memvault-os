"""Check 7: empty_content — block content is missing or too short to be useful."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MemoryBlock

DEFAULT_MIN_CHARS = 20


async def check_empty_content(
    db: AsyncSession,
    space_id: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list:
    from ..lint import LintFinding

    bq = select(MemoryBlock.id, MemoryBlock.content, MemoryBlock.block_type).where(
        MemoryBlock.space_id == space_id,
        MemoryBlock.deleted_at.is_(None),
        MemoryBlock.invalid_at.is_(None),
    )
    findings: list = []
    for bid, content, btype in (await db.execute(bq)).all():
        text = (content or "").strip()
        if len(text) >= min_chars:
            continue
        findings.append(
            LintFinding(
                check="empty_content",
                severity="warning",
                entity_id=bid,
                entity_type="block",
                message=(
                    f"Block {bid[:8]} ({btype}) has only {len(text)} chars "
                    f"(< {min_chars})"
                ),
                suggested_action=(
                    "Soft-delete or merge into a richer block; if intentionally "
                    "terse, mark with block_type='general' to silence this check."
                ),
                metadata={
                    "block_id": bid,
                    "content_len": len(text),
                    "min_chars": min_chars,
                },
            )
        )
    return findings
