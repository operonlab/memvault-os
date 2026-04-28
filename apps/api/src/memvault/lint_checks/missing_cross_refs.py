"""Check 5: missing_cross_refs — block text mentions an EntityCanonical name
but no Triple links the block's session to that entity.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..kg_models import EntityCanonical, Triple
from ..models import MemoryBlock

# Latin char range — used to decide whether word-boundary matching applies.
# CJK names (e.g. 「李四」) have no whitespace boundary in source text, so we
# keep simple substring matching for them.
_LATIN_RE = re.compile(r"[A-Za-z]")


def _name_in_text(name_lc: str, content_lc: str) -> bool:
    """Whole-word match for Latin names; substring match for CJK / mixed.

    Protects against 'Lisp' inside 'Lispy' or 'Art' inside 'artisan' false
    positives. CJK names fall through to substring (no word boundaries in
    Chinese/Japanese text).
    """
    if not name_lc or not content_lc:
        return False
    if _LATIN_RE.search(name_lc):
        pattern = r"\b" + re.escape(name_lc) + r"\b"
        return re.search(pattern, content_lc) is not None
    return name_lc in content_lc


async def check_missing_cross_refs(
    db: AsyncSession,
    space_id: str,
    *,
    sample_blocks: int = 300,
    min_name_len: int = 4,
) -> list:
    from ..lint import LintFinding

    # Load known canonical entities
    eq = select(EntityCanonical.id, EntityCanonical.canonical_name).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    entities = [
        (eid, name)
        for eid, name in (await db.execute(eq)).all()
        if name and len(name) >= min_name_len
    ]
    if not entities:
        return []

    # Build name → entity_id index (lowercased) AND a parallel original-case
    # map so findings surface the human-readable name (e.g. "PostgreSQL")
    # rather than the lowercased lookup key.
    name_to_eid: dict[str, str] = {n.lower(): eid for eid, n in entities}
    name_original: dict[str, str] = {n.lower(): n for eid, n in entities}

    # Sample blocks
    bq = (
        select(
            MemoryBlock.id,
            MemoryBlock.content,
            MemoryBlock.source_session,
        )
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.invalid_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(sample_blocks)
    )
    blocks = (await db.execute(bq)).all()

    if not blocks:
        return []

    # All sessions involved
    sessions = {b.source_session for b in blocks if b.source_session}
    triples_by_session: dict[str, set[str]] = {}
    if sessions:
        tq = select(
            Triple.source_session,
            Triple.canonical_subject_id,
            Triple.canonical_object_id,
        ).where(
            Triple.space_id == space_id,
            Triple.invalid_at.is_(None),
            Triple.source_session.in_(sessions),
        )
        for sess, sub_id, obj_id in (await db.execute(tq)).all():
            bag = triples_by_session.setdefault(sess, set())
            if sub_id:
                bag.add(sub_id)
            if obj_id:
                bag.add(obj_id)

    findings: list = []
    for b in blocks:
        content_lc = (b.content or "").lower()
        if not content_lc:
            continue
        sess = b.source_session
        linked_eids = triples_by_session.get(sess, set()) if sess else set()
        mentioned: list[tuple[str, str, str]] = []  # (name_lc, eid, original)
        for name_lc, eid in name_to_eid.items():
            if _name_in_text(name_lc, content_lc) and eid not in linked_eids:
                mentioned.append((name_lc, eid, name_original[name_lc]))
                if len(mentioned) >= 5:
                    break
        if not mentioned:
            continue

        findings.append(
            LintFinding(
                check="missing_cross_refs",
                severity="info",
                entity_id=b.id,
                entity_type="block",
                message=(
                    f"Block {b.id[:8]} mentions {len(mentioned)} entity names "
                    f"with no triple link (e.g. {mentioned[0][2]!r})"
                ),
                suggested_action=(
                    "Re-run triple extraction over this block, or add manual "
                    "links to the missing entities."
                ),
                metadata={
                    "block_id": b.id,
                    "missing_links": [
                        {"name": orig, "entity_id": eid} for _lc, eid, orig in mentioned
                    ],
                },
            )
        )

    return findings
