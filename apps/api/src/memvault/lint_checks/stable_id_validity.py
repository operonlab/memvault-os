"""Check 9: stable_id_validity — block IDs must be 32-char lowercase hex
(uuid7 hex) and unique within a space.

Workshop convention: all IDs are stored as uuid7().hex → 32 hex chars. We flag:
   - any block.id failing this regex (corrupt or migrated row)
   - duplicates across active rows (should be impossible with PK, but cheap to
     verify)
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MemoryBlock

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


async def check_stable_id_validity(
    db: AsyncSession,
    space_id: str,
) -> list:
    from ..lint import LintFinding

    bq = select(MemoryBlock.id).where(
        MemoryBlock.space_id == space_id,
        MemoryBlock.deleted_at.is_(None),
    )
    ids = [r[0] for r in (await db.execute(bq)).all()]

    findings: list = []
    seen: set[str] = set()
    for bid in ids:
        if bid in seen:
            findings.append(
                LintFinding(
                    check="stable_id_validity",
                    severity="error",
                    entity_id=bid,
                    entity_type="block",
                    message=f"Duplicate block_id detected: {bid}",
                    suggested_action=(
                        "Investigate ingestion pipeline — IDs should be "
                        "uuid7-generated and unique."
                    ),
                    metadata={"block_id": bid, "issue": "duplicate"},
                )
            )
            continue
        seen.add(bid)
        if not bid or not _HEX32.match(bid):
            findings.append(
                LintFinding(
                    check="stable_id_validity",
                    severity="error",
                    entity_id=bid or "",
                    entity_type="block",
                    message=(
                        f"Invalid block_id format: {bid!r} "
                        "(expected 32 lowercase hex chars)"
                    ),
                    suggested_action=(
                        "Re-issue a uuid7 hex id; this row was likely created "
                        "by a non-standard path."
                    ),
                    metadata={"block_id": bid, "issue": "format"},
                )
            )

    # Cross-check: SQL count should match
    cq = select(func.count()).select_from(MemoryBlock).where(
        MemoryBlock.space_id == space_id,
        MemoryBlock.deleted_at.is_(None),
    )
    sql_count = (await db.execute(cq)).scalar_one()
    if sql_count != len(ids):
        findings.append(
            LintFinding(
                check="stable_id_validity",
                severity="warning",
                entity_id="",
                entity_type="system",
                message=(
                    f"Row count mismatch: SQL={sql_count}, scanned={len(ids)} "
                    "— retry the lint."
                ),
                suggested_action="Retry; transient DB read inconsistency.",
                metadata={"sql_count": sql_count, "scanned_count": len(ids)},
            )
        )

    return findings
