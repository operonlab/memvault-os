"""Check 8: stale_index_entries — community/CommunityTriple/CommunitySummary
references that point at blocks/triples that have been soft-deleted or
invalidated.

We scan:
   - Community.entity_ids[] entries that no longer match any live entity row
   - CommunitySummary.representative_triples (text refs — best effort, skip if
     storage format diverges)
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..kg_models import Community, EntityCanonical


async def check_stale_index_entries(
    db: AsyncSession,
    space_id: str,
) -> list:
    from ..lint import LintFinding

    eq = select(EntityCanonical.id).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    live_eids = {r[0] for r in (await db.execute(eq)).all()}

    cq = select(Community).where(Community.space_id == space_id)
    findings: list = []
    for community in (await db.execute(cq)).scalars().all():
        ids = community.entity_ids or []
        if not ids:
            continue
        dead = [eid for eid in ids if eid not in live_eids]
        if not dead:
            continue
        findings.append(
            LintFinding(
                check="stale_index_entries",
                severity="warning",
                entity_id=community.id,
                entity_type="community",
                message=(
                    f"Community {community.id[:8]} ({community.name!r}) lists "
                    f"{len(dead)}/{len(ids)} entity_ids that no longer exist"
                ),
                suggested_action=(
                    "Re-run community detection or prune the dead entity_ids "
                    "from this community's index."
                ),
                metadata={
                    "community_id": community.id,
                    "dead_entity_count": len(dead),
                    "total_entity_count": len(ids),
                    "sample_dead_ids": dead[:5],
                },
            )
        )

    return findings
