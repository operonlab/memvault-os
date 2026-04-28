"""W3 — positive/negative tests for critical lint check: dead_triples.

Mock-based unit test: stub AsyncSession.execute to return either entity-id rows
or Triple objects depending on call order.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from unittest.mock import AsyncMock, MagicMock

from src.memvault.lint_checks.dead_triples import check_dead_triples


def _make_triple(tid: str, subj_id: str | None, obj_id: str | None):
    t = MagicMock()
    t.id = tid
    t.canonical_subject_id = subj_id
    t.canonical_object_id = obj_id
    return t


def _make_db(live_entity_ids: list[str], triples: list):
    """Build an AsyncSession-shaped mock.

    First execute() → entity-id rows (.all() → list of (id,) tuples).
    Second execute() → triple result whose .scalars().all() returns triples.
    """
    eid_result = MagicMock()
    eid_result.all = MagicMock(return_value=[(eid,) for eid in live_entity_ids])

    triple_scalars = MagicMock()
    triple_scalars.all = MagicMock(return_value=triples)
    triple_result = MagicMock()
    triple_result.scalars = MagicMock(return_value=triple_scalars)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[eid_result, triple_result])
    return db


@pytest.mark.asyncio
async def test_dead_triples_reports_dangling_target():
    # subject points to a missing entity → finding expected
    db = _make_db(
        live_entity_ids=["e1" * 16],
        triples=[_make_triple("t1" * 16, "e1" * 16, "ghost-target-id")],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert len(findings) == 1
    assert findings[0].check == "dead_triples"
    assert findings[0].entity_type == "triple"
    assert "ghost" in findings[0].metadata["missing"][0]["entity_id"]


@pytest.mark.asyncio
async def test_dead_triples_clean_no_findings():
    # both subject + object live → no findings
    db = _make_db(
        live_entity_ids=["e1" * 16, "e2" * 16],
        triples=[_make_triple("t1" * 16, "e1" * 16, "e2" * 16)],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert findings == []
