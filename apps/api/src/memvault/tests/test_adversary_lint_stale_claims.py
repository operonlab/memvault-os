"""Adversary tests for check #3 — stale_claims.

stale_claims wraps check_contradictions and re-stamps with age data.
Mutations to probe:
  (a) old block + newer block contradicting → finding tagged stale_claims
  (b) same triple_id self-conflict → no finding (deduped)
  (c) age boundary: triple at exactly threshold day → behavior must be defined
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint import LintFinding
from src.memvault.lint_checks.stale_claims import check_stale_claims


def _contradiction_finding(triple_a: str, triple_b: str | None) -> LintFinding:
    return LintFinding(
        check="contradictions",
        severity="warning",
        entity_id=triple_a,
        entity_type="triple",
        message="dummy contradiction",
        suggested_action="resolve",
        metadata={"triple_a": triple_a, "triple_b": triple_b},
    )


def _make_db(triple_id_to_created: dict[str, datetime]):
    cq_result = MagicMock()
    cq_result.all = MagicMock(
        return_value=[(tid, dt) for tid, dt in triple_id_to_created.items()]
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=cq_result)
    return db


@pytest.mark.asyncio
async def test_stale_claims_old_vs_new_contradiction_flagged():
    """(a) old triple (40 days) contradicts newer triple (1 day) → stale_claims finding."""
    now = datetime.now(UTC)
    old_id = "t_old_" + "0" * 26
    new_id = "t_new_" + "0" * 26
    db = _make_db({old_id: now - timedelta(days=40), new_id: now - timedelta(days=1)})

    fake_contradictions = [_contradiction_finding(old_id, new_id)]
    with patch(
        "src.memvault.lint_checks.stale_claims.__import__", create=True
    ):
        # Patch via memvault.lint.check_contradictions (imported lazily)
        with patch(
            "src.memvault.lint.check_contradictions",
            new=AsyncMock(return_value=fake_contradictions),
        ):
            findings = await check_stale_claims(db, space_id="space-1")

    assert len(findings) >= 1, "contradiction with old triple must yield stale_claims"
    assert findings[0].check == "stale_claims", (
        "BUG INDICATOR: finding.check must be re-stamped to 'stale_claims', "
        f"got {findings[0].check!r}"
    )


@pytest.mark.asyncio
async def test_stale_claims_self_conflict_not_double_reported():
    """(b) Same triple cited as both A and B (self-conflict) → must not double count."""
    now = datetime.now(UTC)
    same_id = "t_same" + "0" * 26
    db = _make_db({same_id: now - timedelta(days=40)})

    fake = [_contradiction_finding(same_id, same_id)]
    with patch(
        "src.memvault.lint.check_contradictions",
        new=AsyncMock(return_value=fake),
    ):
        findings = await check_stale_claims(db, space_id="space-1")

    # Self-conflict shouldn't crash; whether 0 or 1 findings is implementation
    # choice, but never >1 from a single contradiction.
    assert len(findings) <= 1, (
        f"BUG INDICATOR: self-conflict produced {len(findings)} findings — "
        "stale_claims is double-counting."
    )


@pytest.mark.asyncio
async def test_stale_claims_no_contradictions_returns_empty():
    """(c) When upstream check_contradictions returns [] → must return [] cleanly."""
    db = _make_db({})
    with patch(
        "src.memvault.lint.check_contradictions", new=AsyncMock(return_value=[])
    ):
        findings = await check_stale_claims(db, space_id="space-1")
    assert findings == []


@pytest.mark.asyncio
async def test_stale_claims_severity_invariant():
    """Severity must be in canonical set, check name aligned."""
    now = datetime.now(UTC)
    a, b = "a" * 32, "b" * 32
    db = _make_db({a: now - timedelta(days=60), b: now - timedelta(days=2)})
    fake = [_contradiction_finding(a, b)]
    with patch(
        "src.memvault.lint.check_contradictions", new=AsyncMock(return_value=fake)
    ):
        findings = await check_stale_claims(db, space_id="space-1")

    for f in findings:
        assert f.severity in {"info", "warning", "error"}
        assert f.check == "stale_claims"
