"""Check 3: stale_claims — wraps existing check_contradictions.

Per Worker 3 spec: "stale_claims = block.created_at older than newer block by
N days AND assertion conflicts". The existing `check_contradictions` already
detects assertion conflicts via Qdrant + same-subject+predicate match. We
re-emit those findings under the `stale_claims` label, adding an age-days
heuristic by reading the related triples' created_at.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..kg_models import Triple


async def check_stale_claims(
    db: AsyncSession,
    space_id: str,
    *,
    age_days_threshold: int = 30,
    sample_size: int = 100,
) -> list:
    """Detect stale assertions by deferring to check_contradictions and tagging age."""
    from ..lint import LintFinding, check_contradictions

    base = await check_contradictions(
        db,
        space_id,
        sample_size=sample_size,
    )

    # Re-stamp findings as `stale_claims`, attach age info
    findings: list = []
    triple_ids: set[str] = set()
    for f in base:
        if f.check != "contradictions":
            continue
        meta = f.metadata or {}
        ta, tb = meta.get("triple_a"), meta.get("triple_b")
        if ta:
            triple_ids.add(ta)
        if tb:
            triple_ids.add(tb)

    created_map: dict[str, datetime] = {}
    if triple_ids:
        cq = select(Triple.id, Triple.created_at).where(Triple.id.in_(triple_ids))
        for tid, created in (await db.execute(cq)).all():
            if created is not None:
                created_map[tid] = created

    now = datetime.now(UTC)
    for f in base:
        if f.check != "contradictions":
            continue
        meta = dict(f.metadata or {})
        ta = meta.get("triple_a")
        tb = meta.get("triple_b")
        a_at = created_map.get(ta) if ta else None
        b_at = created_map.get(tb) if tb else None
        age_days = None
        if a_at and b_at:
            age_days = abs((a_at - b_at).days)
        elif a_at:
            age_days = (now - a_at).days
        elif b_at:
            age_days = (now - b_at).days

        if age_days is not None and age_days < age_days_threshold:
            # Conflict but not yet stale by the age threshold — skip
            continue

        meta["age_days"] = age_days
        meta["age_threshold"] = age_days_threshold
        findings.append(
            LintFinding(
                check="stale_claims",
                severity="warning",
                entity_id=f.entity_id,
                entity_type="triple",
                message=(
                    f"Stale claim (age={age_days}d ≥ {age_days_threshold}d): {f.message}"
                ),
                suggested_action=(
                    "Verify which triple reflects current state; invalidate the "
                    "older claim with reason='stale_claim'."
                ),
                metadata=meta,
            )
        )

    return findings
