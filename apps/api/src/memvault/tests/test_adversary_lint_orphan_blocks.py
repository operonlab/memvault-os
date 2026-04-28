"""Adversary tests for check #1 — orphan_blocks.

Mutation-resistant: probes boundary semantics of "orphan" definition. The spec
says: a block is orphan when no triple references it. Implementation likely
uses block.source_session ∈ referenced_session set as the cheap proxy. These
tests exercise:
  (a) block whose session is unknown to any triple → finding
  (b) block whose session IS referenced by ≥1 triple → no finding
  (c) empty space → empty list, NOT exception
  (d) invariant: severity ∈ {info, warning, error}, finding.check == "orphan_blocks"
  (e) mutation guard: if author flips `if sess in referenced_sessions: continue`
      to `if sess not in referenced_sessions: continue`, case (a) catches it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from unittest.mock import AsyncMock, MagicMock

from src.memvault.lint_checks.orphan_blocks import check_orphan_blocks

_VALID_SEVERITIES = {"info", "warning", "error"}


def _make_block(bid: str, session: str | None, btype: str = "general"):
    b = MagicMock()
    b.id = bid
    b.source_session = session
    b.block_type = btype
    return b


def _make_db(referenced_sessions: list[str], blocks_batches: list[list]):
    """Mock AsyncSession.

    First execute() → distinct session rows (.all() → list of (session,))
    Subsequent execute() → block scalars batches; final empty batch ends loop.
    """
    sess_result = MagicMock()
    sess_result.all = MagicMock(return_value=[(s,) for s in referenced_sessions])

    side_effects = [sess_result]
    for batch in blocks_batches:
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=batch)
        block_result = MagicMock()
        block_result.scalars = MagicMock(return_value=scalars)
        side_effects.append(block_result)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effects)
    return db


@pytest.mark.asyncio
async def test_orphan_block_no_inbound_session_flagged():
    """(a) Block whose session is NOT referenced by any triple → must report."""
    block = _make_block("a" * 32, session="ghost-session")
    db = _make_db(referenced_sessions=[], blocks_batches=[[block], []])

    findings = await check_orphan_blocks(db, space_id="space-1")

    assert len(findings) == 1, "single orphan block must surface exactly one finding"
    f = findings[0]
    # invariants
    assert f.check == "orphan_blocks", "check name must match registry id"
    assert f.severity in _VALID_SEVERITIES, f"severity {f.severity!r} not in valid set"
    assert f.entity_type == "block"
    assert f.entity_id == "a" * 32


@pytest.mark.asyncio
async def test_orphan_block_with_referenced_session_silent():
    """(b) Block whose session IS referenced by a triple → no finding."""
    block = _make_block("b" * 32, session="live-sess")
    db = _make_db(referenced_sessions=["live-sess"], blocks_batches=[[block], []])

    findings = await check_orphan_blocks(db, space_id="space-1")

    assert findings == [], (
        "block with referenced session must not be reported. "
        "If this fails, the orphan predicate is inverted."
    )


@pytest.mark.asyncio
async def test_orphan_empty_space_returns_empty_list_not_exception():
    """(c) Empty space → return [] cleanly, no IndexError / KeyError."""
    db = _make_db(referenced_sessions=[], blocks_batches=[[]])

    # Must NOT raise
    findings = await check_orphan_blocks(db, space_id="empty-space")
    assert findings == []
    assert isinstance(findings, list)


@pytest.mark.asyncio
async def test_orphan_block_with_null_session_flagged():
    """(d) Block with source_session=NULL has no parent triple → orphan.

    Mutation guard: if author writes `if sess and sess in ref` and short-circuits
    on null, NULL-session blocks would be silently skipped — a real-world hole
    where seeded/manual blocks never get linted.
    """
    block = _make_block("c" * 32, session=None)
    db = _make_db(referenced_sessions=["other-sess"], blocks_batches=[[block], []])

    findings = await check_orphan_blocks(db, space_id="space-1")
    # Spec says: "no triple references" — null-session block by definition has none.
    # If implementation skips null-session, this test EXPOSES the hole.
    assert len(findings) == 1, (
        "BUG INDICATOR: block with NULL source_session should still be reported "
        "as orphan — it has no inbound triple by definition."
    )
