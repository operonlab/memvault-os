"""Adversary tests for check #9 — stable_id_validity (CRITICAL).

Complements existing test_lint_stable_id_validity.py with adversarial cases:
  (a) UUID v4 with dashes (8-4-4-4-12 = 36 chars) — spec says "32-hex" so
      dashes should fail; mutation guard.
  (b) Mixed valid + invalid in same scan → only invalid surfaces.
  (c) Duplicate IDs scenario.
  (d) UUID-like but with one non-hex char ('z') in the middle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.stable_id_validity import check_stable_id_validity


def _make_db(block_ids: list[str], sql_count: int | None = None):
    id_result = MagicMock()
    id_result.all = MagicMock(return_value=[(bid,) for bid in block_ids])

    count_result = MagicMock()
    count_result.scalar_one = MagicMock(
        return_value=sql_count if sql_count is not None else len(block_ids)
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[id_result, count_result])
    return db


@pytest.mark.asyncio
async def test_stable_id_dashed_uuid_flagged():
    """(a) Standard UUID with dashes should fail _HEX32 (32 lowercase hex, no dashes)."""
    dashed = "0190abcd-1234-7abc-89de-0123456789ab"  # 36 chars
    db = _make_db(block_ids=[dashed])
    findings = await check_stable_id_validity(db, space_id="space-1")
    assert len(findings) >= 1, (
        "BUG INDICATOR: dashed UUID was accepted as valid 32-hex. "
        "Spec says 32-char lowercase hex without dashes."
    )
    assert findings[0].check == "stable_id_validity"


@pytest.mark.asyncio
async def test_stable_id_mixed_valid_and_invalid():
    """(b) Two blocks: one valid 32-hex, one all-uppercase → only invalid flagged."""
    valid = "a" * 32
    invalid = "A" * 32  # uppercase fails _HEX32 (lowercase only per convention)
    db = _make_db(block_ids=[valid, invalid])
    findings = await check_stable_id_validity(db, space_id="space-1")
    flagged_ids = {f.entity_id for f in findings}
    assert valid not in flagged_ids, "valid 32-lowercase-hex should NOT be flagged"
    assert invalid in flagged_ids, (
        "BUG INDICATOR: uppercase 32-char hex was accepted; spec is lowercase-only."
    )


@pytest.mark.asyncio
async def test_stable_id_duplicate_detected():
    """(c) Same id appears twice in scan → 'duplicate' issue surfaces."""
    dup = "f" * 32
    db = _make_db(block_ids=[dup, dup])
    findings = await check_stable_id_validity(db, space_id="space-1")
    dup_findings = [f for f in findings if f.metadata.get("issue") == "duplicate"]
    assert len(dup_findings) >= 1, (
        "BUG INDICATOR: duplicate block_id not flagged with issue='duplicate'."
    )


@pytest.mark.asyncio
async def test_stable_id_non_hex_char_in_middle_flagged():
    """(d) 32-char string but with 'z' in the middle → format finding."""
    bad = "a" * 16 + "z" + "b" * 15  # 32 chars, but 'z' is not hex
    assert len(bad) == 32
    db = _make_db(block_ids=[bad])
    findings = await check_stable_id_validity(db, space_id="space-1")
    assert len(findings) >= 1, (
        "BUG INDICATOR: 32-char string with non-hex char 'z' was accepted."
    )
    assert findings[0].metadata.get("issue") == "format"


@pytest.mark.asyncio
async def test_stable_id_severity_invariant_critical():
    """Critical-tier check: severity must NOT be 'info'."""
    db = _make_db(block_ids=["short-id"])
    findings = await check_stable_id_validity(db, space_id="space-1")
    assert len(findings) >= 1
    assert findings[0].severity in {"warning", "error"}, (
        f"BUG INDICATOR: critical check stable_id_validity emitted severity "
        f"{findings[0].severity!r} which won't surface in critical bucket."
    )
