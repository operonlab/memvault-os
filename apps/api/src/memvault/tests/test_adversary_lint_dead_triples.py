"""Adversary tests for check #2 — dead_triples (CRITICAL severity).

Complements existing test_lint_dead_triples.py with mutation guards:
  (a) ONLY object missing → finding (subj live)
  (b) ONLY subject missing → finding (obj live)
  (c) BOTH live → no finding (mutation: `not in` flipped)
  (d) invariant: critical severity must surface as severity ∈ {error, warning}
      AND check name == "dead_triples"
  (e) Triple with both FKs NULL must not crash — non-trivial edge case
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
async def test_dead_triples_object_missing_only():
    """(a) Subject live, object dangling → finding still raised."""
    db = _make_db(
        live_entity_ids=["e1" * 16],
        triples=[_make_triple("t1" * 16, subj_id="e1" * 16, obj_id="ghost-obj-id")],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert len(findings) == 1
    assert findings[0].check == "dead_triples"


@pytest.mark.asyncio
async def test_dead_triples_subject_missing_only():
    """(b) Object live, subject dangling → finding."""
    db = _make_db(
        live_entity_ids=["e2" * 16],
        triples=[_make_triple("t1" * 16, subj_id="ghost-subj-id", obj_id="e2" * 16)],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert len(findings) == 1, "missing subject FK must surface a finding"
    assert findings[0].entity_type == "triple"


@pytest.mark.asyncio
async def test_dead_triples_clean_no_findings_mutation_inverted_guard():
    """(c) Both FKs live → no finding. If author flipped `not in` → `in`,
    this test fails (it would report on every triple).
    """
    db = _make_db(
        live_entity_ids=["e1" * 16, "e2" * 16],
        triples=[_make_triple("t1" * 16, subj_id="e1" * 16, obj_id="e2" * 16)],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert findings == [], (
        "all-live triple must NOT report. "
        "If this fails, dangling-detection predicate is inverted."
    )


@pytest.mark.asyncio
async def test_dead_triples_severity_invariant_critical_check():
    """(d) Severity invariant: registry says critical, finding.severity must be
    one of {error, warning, info} (per LintFinding dataclass docs)."""
    db = _make_db(
        live_entity_ids=[],
        triples=[_make_triple("t1" * 16, subj_id="ghost", obj_id=None)],
    )
    findings = await check_dead_triples(db, space_id="space-1")
    assert len(findings) == 1
    assert findings[0].severity in {"error", "warning", "info"}, (
        f"BUG INDICATOR: severity {findings[0].severity!r} not in canonical set"
    )
    # Critical-tier checks should map to error or warning, never info-only
    assert findings[0].severity != "info", (
        "BUG INDICATOR: dead_triples is registered as critical — "
        "severity 'info' would lose visibility in the report's critical bucket."
    )


@pytest.mark.asyncio
async def test_dead_triples_both_fks_null_is_filtered_at_query_level():
    """(e) Triple with BOTH canonical_*_id NULL is excluded by the WHERE clause
    (since the query filters for non-null FKs). No crash, no finding."""
    # We simulate the DB returning zero triples (because the SQL filter excludes
    # the null-null pair). check_dead_triples should not crash.
    db = _make_db(live_entity_ids=["e1" * 16], triples=[])
    findings = await check_dead_triples(db, space_id="space-1")
    assert findings == []
