"""Check 2: dead_triples — triples whose canonical_subject_id / canonical_object_id
points at an entity row that no longer exists (or is soft-deleted).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..kg_models import EntityCanonical, Triple


async def check_dead_triples(
    db: AsyncSession,
    space_id: str,
) -> list:
    from ..lint import LintFinding

    # Live entity ids (not soft-deleted)
    eid_q = select(EntityCanonical.id).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    live_ids = {r[0] for r in (await db.execute(eid_q)).all()}

    # Active triples with a non-null FK
    tq = select(Triple).where(
        Triple.space_id == space_id,
        Triple.invalid_at.is_(None),
        (
            Triple.canonical_subject_id.isnot(None)
            | Triple.canonical_object_id.isnot(None)
        ),
    )
    findings: list = []
    for t in (await db.execute(tq)).scalars().all():
        problems = []
        if t.canonical_subject_id and t.canonical_subject_id not in live_ids:
            problems.append(("subject", t.canonical_subject_id))
        if t.canonical_object_id and t.canonical_object_id not in live_ids:
            problems.append(("object", t.canonical_object_id))
        if not problems:
            continue
        descr = ", ".join(f"{role}→{eid[:8]}" for role, eid in problems)
        findings.append(
            LintFinding(
                check="dead_triples",
                severity="error",
                entity_id=t.id,
                entity_type="triple",
                message=(
                    f"Triple {t.id[:8]} references missing entity row(s): {descr}"
                ),
                suggested_action=(
                    "Re-run entity resolution for this triple, or invalidate the "
                    "triple if the referenced entity was intentionally removed."
                ),
                metadata={
                    "triple_id": t.id,
                    "missing": [
                        {"role": role, "entity_id": eid} for role, eid in problems
                    ],
                },
            )
        )
    return findings
