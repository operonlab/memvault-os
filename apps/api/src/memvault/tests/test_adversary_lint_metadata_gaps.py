"""Adversary tests for check #6 — metadata_gaps.

Spec: required block columns (e.g., tags for type='knowledge') blank/null → finding.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.metadata_gaps import check_metadata_gaps


def _make_block(
    bid: str,
    block_type: str = "knowledge",
    tags: list[str] | None = None,
    confidence: float | None = None,
    content: str = "some valid content",
    source_session: str | None = "sess-1",
):
    b = MagicMock()
    b.id = bid
    b.block_type = block_type
    b.tags = tags if tags is not None else []
    b.confidence = confidence
    b.content = content
    b.source_session = source_session
    return b


def _make_db(blocks: list):
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=blocks)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_metadata_gaps_knowledge_block_no_tags_flagged():
    """(a) knowledge block with empty tags → finding."""
    block = _make_block("a" * 32, block_type="knowledge", tags=[])
    db = _make_db([block])
    findings = await check_metadata_gaps(db, space_id="space-1")
    assert len(findings) >= 1, (
        "knowledge block with no tags must be flagged as metadata gap"
    )
    assert findings[0].check == "metadata_gaps"


@pytest.mark.asyncio
async def test_metadata_gaps_complete_block_silent():
    """(b) knowledge block with tags + confidence → no finding."""
    block = _make_block(
        "b" * 32,
        block_type="knowledge",
        tags=["python", "fastapi"],
        confidence=0.9,
    )
    db = _make_db([block])
    findings = await check_metadata_gaps(db, space_id="space-1")
    assert findings == [], (
        "BUG INDICATOR: fully-populated knowledge block surfaced a gap finding."
    )


@pytest.mark.asyncio
async def test_metadata_gaps_severity_invariant():
    """Invariant: every gap finding has check name aligned and valid severity."""
    block = _make_block("c" * 32, block_type="knowledge", tags=[])
    db = _make_db([block])
    findings = await check_metadata_gaps(db, space_id="space-1")
    for f in findings:
        assert f.check == "metadata_gaps"
        assert f.severity in {"info", "warning", "error"}
        assert f.entity_type == "block"


@pytest.mark.asyncio
async def test_metadata_gaps_non_knowledge_block_no_tag_check():
    """(c) Non-knowledge block (e.g. 'general') with empty tags → must NOT
    be flagged on the tags axis.

    Mutation guard: if author wrote `if not block.tags` without filtering by
    block_type, every general/skill block without tags would be reported,
    swamping the report."""
    block = _make_block(
        "d" * 32, block_type="general", tags=[], content="legit content here"
    )
    db = _make_db([block])
    findings = await check_metadata_gaps(db, space_id="space-1")
    # general blocks aren't required to have tags per default config
    tag_gap_findings = [
        f for f in findings if "tags" in (f.message.lower() + str(f.metadata).lower())
    ]
    assert tag_gap_findings == [], (
        "BUG INDICATOR: 'general' block (not in flag_missing_tags_for_types) "
        "should NOT raise a tags-gap finding."
    )
