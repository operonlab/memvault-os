"""W3 — positive/negative tests for critical lint check: stable_id_validity.

Mock-based unit test: validates 32-char hex enforcement and SQL count cross-check.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from unittest.mock import AsyncMock, MagicMock

from src.memvault.lint_checks.stable_id_validity import check_stable_id_validity


def _make_db(block_ids: list[str], sql_count: int | None = None):
    """First execute() → block-id rows; second execute() → scalar count."""
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
async def test_stable_id_validity_flags_bad_format():
    # uppercase + too-short id → format finding
    db = _make_db(block_ids=["NOT-A-VALID-UUID7-HEX"])
    findings = await check_stable_id_validity(db, space_id="space-1")
    assert len(findings) == 1
    assert findings[0].check == "stable_id_validity"
    assert findings[0].metadata["issue"] == "format"


@pytest.mark.asyncio
async def test_stable_id_validity_clean_no_findings():
    # valid 32-char lowercase hex → no findings
    valid_a = "a" * 32
    valid_b = "b" * 16 + "0" * 16
    db = _make_db(block_ids=[valid_a, valid_b])
    findings = await check_stable_id_validity(db, space_id="space-1")
    assert findings == []
