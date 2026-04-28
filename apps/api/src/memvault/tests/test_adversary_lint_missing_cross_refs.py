"""Adversary tests for check #5 — missing_cross_refs.

Spec: a block's text mentions a known canonical entity name, but no triple
links the block's session to that entity → finding.
Mutations to probe:
  (a) entity name appears in block text, NO triple → finding
  (b) entity name appears AND triple already links it → no finding
  (c) entity name is a substring inside a longer word ("art" inside "artisan")
      → must NOT trigger false positive
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.missing_cross_refs import check_missing_cross_refs


def _make_db(
    entities: list[tuple[str, str]],
    blocks: list[tuple[str, str, str | None]],
    triples: list[tuple[str | None, str | None, str | None]],
):
    """Mock execute returns: entities → blocks → triples in order.

    entities: list of (entity_id, canonical_name)
    blocks: list of (block_id, content, source_session)
    triples: list of (source_session, canonical_subject_id, canonical_object_id)
    """
    eq_result = MagicMock()
    eq_result.all = MagicMock(return_value=entities)

    bq_result = MagicMock()
    bq_result.all = MagicMock(
        return_value=[
            MagicMock(id=bid, content=c, source_session=s) for bid, c, s in blocks
        ]
    )
    # Some impls iterate via positional unpacking — the real query is a tuple result
    # Provide compatibility by also making it iterable
    bq_result.all = MagicMock(
        return_value=[
            type("Row", (), {"id": bid, "content": c, "source_session": s})()
            for bid, c, s in blocks
        ]
    )

    tq_result = MagicMock()
    tq_result.all = MagicMock(return_value=triples)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[eq_result, bq_result, tq_result])
    return db


@pytest.mark.asyncio
async def test_missing_cross_refs_unlinked_mention_flagged():
    """(a) Entity 'PostgreSQL' in canonical, mentioned in block, no triple link → finding."""
    entities = [("e" + "0" * 31, "PostgreSQL")]
    blocks = [("b" + "0" * 31, "We migrated to PostgreSQL last week.", "sess-1")]
    triples: list = []  # no triple ties sess-1 to entity
    db = _make_db(entities, blocks, triples)

    findings = await check_missing_cross_refs(db, space_id="space-1")
    pg_findings = [f for f in findings if "PostgreSQL" in (f.message + str(f.metadata))]
    assert pg_findings, (
        "BUG INDICATOR: entity mentioned in block text without a linking triple "
        "must be flagged."
    )


@pytest.mark.asyncio
async def test_missing_cross_refs_already_linked_silent():
    """(b) Entity already linked via triple in same session → no finding."""
    entities = [("e" + "0" * 31, "PostgreSQL")]
    blocks = [("b" + "0" * 31, "We migrated to PostgreSQL last week.", "sess-1")]
    triples = [("sess-1", "e" + "0" * 31, None)]  # subject FK = our entity
    db = _make_db(entities, blocks, triples)

    findings = await check_missing_cross_refs(db, space_id="space-1")
    pg_findings = [f for f in findings if "PostgreSQL" in (f.message + str(f.metadata))]
    assert pg_findings == [], (
        "BUG INDICATOR: triple already links session→entity, "
        "should not flag as missing cross-ref."
    )


@pytest.mark.asyncio
async def test_missing_cross_refs_substring_false_positive_guard():
    """(c) entity name 'Art' (capitalised) is short and could match inside
    'artisan' / 'cartography' if substring-search is used naively.

    With min_name_len=4 default, 'Art' (3 chars) is filtered. Test that
    a 4-char entity 'Lisp' inside 'Lispy' doesn't trigger — proper word-boundary."""
    entities = [("e" + "0" * 31, "Lisp")]  # 4 chars, passes min_name_len
    blocks = [("b" + "0" * 31, "We use Lispy syntax in our DSL.", "sess-1")]
    db = _make_db(entities, blocks, triples=[])
    findings = await check_missing_cross_refs(db, space_id="space-1")
    lisp_findings = [f for f in findings if "Lisp" in str(f.metadata)]
    assert lisp_findings == [], (
        "BUG INDICATOR: substring match without word boundary — 'Lisp' inside "
        "'Lispy' should NOT trigger missing_cross_ref finding."
    )


@pytest.mark.asyncio
async def test_missing_cross_refs_no_entities_returns_empty():
    """No canonical entities → cannot have missing cross-refs → empty list."""
    db = _make_db(entities=[], blocks=[("b" + "0" * 31, "any text", "s")], triples=[])
    findings = await check_missing_cross_refs(db, space_id="space-1")
    assert findings == []
