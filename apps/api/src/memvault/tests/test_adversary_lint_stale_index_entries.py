"""Adversary tests for check #8 — stale_index_entries.

Spec: community/tag index points to a deleted block (or missing entity) → finding.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.stale_index_entries import check_stale_index_entries


def _make_community(cid: str, member_eids: list[str]):
    c = MagicMock()
    c.id = cid
    c.member_entity_ids = member_eids
    c.entity_ids = member_eids  # alt name
    c.members = member_eids  # alt name
    c.name = f"community-{cid[:6]}"
    return c


def _make_db(live_eids: list[str], communities: list):
    eq_result = MagicMock()
    eq_result.all = MagicMock(return_value=[(eid,) for eid in live_eids])

    cq_scalars = MagicMock()
    cq_scalars.all = MagicMock(return_value=communities)
    cq_result = MagicMock()
    cq_result.scalars = MagicMock(return_value=cq_scalars)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[eq_result, cq_result])
    return db


@pytest.mark.asyncio
async def test_stale_index_dead_member_flagged():
    """(a) Community references a deleted entity_id → finding."""
    live = ["e" + "0" * 31]
    community = _make_community("c" + "0" * 31, member_eids=["ghost-eid", "e" + "0" * 31])
    db = _make_db(live_eids=live, communities=[community])
    findings = await check_stale_index_entries(db, space_id="space-1")
    assert len(findings) >= 1, (
        "BUG INDICATOR: community member 'ghost-eid' is not in live entity set "
        "but no stale-index finding was raised."
    )
    assert findings[0].check == "stale_index_entries"


@pytest.mark.asyncio
async def test_stale_index_all_live_silent():
    """(b) All community members exist → no finding."""
    live = ["e" + "0" * 31, "e" + "1" + "0" * 30]
    community = _make_community("c" + "0" * 31, member_eids=live)
    db = _make_db(live_eids=live, communities=[community])
    findings = await check_stale_index_entries(db, space_id="space-1")
    assert findings == [], (
        "BUG INDICATOR: all-live community surfaced a stale-index finding."
    )


@pytest.mark.asyncio
async def test_stale_index_empty_communities_returns_empty():
    """(c) No communities → empty list, no exception."""
    db = _make_db(live_eids=["e" + "0" * 31], communities=[])
    findings = await check_stale_index_entries(db, space_id="space-1")
    assert findings == []


@pytest.mark.asyncio
async def test_stale_index_severity_invariant():
    """All findings: severity ∈ canonical set, check name aligned."""
    live: list[str] = []
    community = _make_community("c" + "0" * 31, member_eids=["dead-1", "dead-2"])
    db = _make_db(live_eids=live, communities=[community])
    findings = await check_stale_index_entries(db, space_id="space-1")
    for f in findings:
        assert f.check == "stale_index_entries"
        assert f.severity in {"info", "warning", "error"}
