"""Adversary tests for check #7 — empty_content.

Spec: block.content < 20 chars → finding.
Boundary mutations:
  (a) content of 5 chars → finding
  (b) content of EXACTLY 20 chars → boundary; if check uses `<=` instead of
      `<`, behavior diverges. We assert the documented spec (`< 20`).
  (c) "   " (3 spaces) → must be flagged; whitespace-only is empty.
  (d) content of 100 chars → no finding
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.empty_content import check_empty_content


def _make_db(rows: list[tuple[str, str | None, str]]):
    """rows: list of (block_id, content, block_type)."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_empty_content_short_string_flagged():
    """(a) 5-char content → finding."""
    rows = [("a" * 32, "short", "general")]
    db = _make_db(rows)
    findings = await check_empty_content(db, space_id="space-1")
    assert len(findings) == 1
    assert findings[0].check == "empty_content"


@pytest.mark.asyncio
async def test_empty_content_boundary_exact_threshold():
    """(b) Boundary: content of EXACTLY 20 chars (default min_chars=20).

    Spec: `< 20` → 20-char content should NOT be flagged.
    Mutation: if author wrote `<= 20`, this triggers a finding."""
    content_20 = "a" * 20
    assert len(content_20) == 20
    rows = [("a" * 32, content_20, "general")]
    db = _make_db(rows)
    findings = await check_empty_content(db, space_id="space-1")
    assert findings == [], (
        "BUG INDICATOR: content of EXACTLY min_chars (20) should NOT be flagged "
        "(spec uses `<`, not `<=`). If this fires, boundary predicate is wrong."
    )


@pytest.mark.asyncio
async def test_empty_content_whitespace_only_flagged():
    """(c) Whitespace-only "   " is functionally empty → must be flagged.

    Mutation: if author uses `len(content) < 20` without `.strip()`,
    a 25-char string of all spaces would NOT be flagged — clearly wrong.
    """
    rows = [("a" * 32, "   " * 10, "general")]  # 30 spaces
    db = _make_db(rows)
    findings = await check_empty_content(db, space_id="space-1")
    assert len(findings) == 1, (
        "BUG INDICATOR: whitespace-only content (30 spaces) was not flagged. "
        "Implementation likely uses raw len() instead of len(content.strip())."
    )


@pytest.mark.asyncio
async def test_empty_content_long_content_silent():
    """(d) 100-char real content → no finding."""
    rows = [("a" * 32, "x" * 100, "general")]
    db = _make_db(rows)
    findings = await check_empty_content(db, space_id="space-1")
    assert findings == []


@pytest.mark.asyncio
async def test_empty_content_null_content_flagged():
    """(e) NULL content (returned as None from DB) → must be flagged, no crash."""
    rows = [("a" * 32, None, "general")]
    db = _make_db(rows)
    # Must not raise TypeError
    findings = await check_empty_content(db, space_id="space-1")
    assert len(findings) == 1, "NULL content should be flagged as empty"
