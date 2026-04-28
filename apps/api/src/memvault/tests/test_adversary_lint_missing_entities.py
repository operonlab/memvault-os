"""Adversary tests for check #4 — missing_entities (KEY ROLE: catch CJK regex hole).

Spec: name mentioned by ≥2 blocks but no canonical entity row → finding.
Mutation focuses:
  (a) English capitalised name in 2+ blocks → finding (positive control)
  (b) **CJK name "李四" in 2+ blocks → SHOULD finding, but reviewer warned
       extractor is regex over capitalised words → 中文人名漏掉**
  (c) name only in 1 block → must NOT report (mutation: < vs <=)
  (d) invariant: no canonical row exists; if one already exists, no finding
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("sqlalchemy")

from src.memvault.lint_checks.missing_entities import check_missing_entities


def _make_db(blocks: list[tuple[str, str]], canonical_names: list[str] | None = None):
    """First execute → block (id, content) rows; later → entity name rows.

    The check internally may query canonicals after collecting candidates;
    we provide a generous side_effect chain to cover varied call counts.
    """
    block_result = MagicMock()
    block_result.all = MagicMock(return_value=blocks)

    entity_result = MagicMock()
    entity_result.all = MagicMock(
        return_value=[(n,) for n in (canonical_names or [])]
    )
    # Some implementations may use .scalars().all() for entity name list
    entity_scalars = MagicMock()
    entity_scalars.all = MagicMock(return_value=canonical_names or [])
    entity_result.scalars = MagicMock(return_value=entity_scalars)

    db = AsyncMock()
    # Provide repeated entity_result so any second/third call gets it
    db.execute = AsyncMock(side_effect=[block_result, entity_result, entity_result])
    return db


@pytest.mark.asyncio
async def test_missing_entities_english_name_in_two_blocks_flagged():
    """(a) Positive control: 'Alice Cooper' in 2 blocks, no canonical row."""
    blocks = [
        ("b" + "0" * 31, "Alice Cooper rocks the stage."),
        ("b" + "1" + "0" * 30, "Yesterday Alice Cooper called me."),
    ]
    db = _make_db(blocks=blocks, canonical_names=[])
    findings = await check_missing_entities(db, space_id="space-1")
    # Expect at least one finding mentioning Alice
    assert any("Alice" in (f.message + str(f.metadata)) for f in findings), (
        "BUG INDICATOR: capitalised name in 2 blocks should surface"
    )


@pytest.mark.asyncio
async def test_missing_entities_cjk_name_hole_documented():
    """(b) **CJK extractor gap**: "李四" appears in 2 blocks; if regex is
    [A-Z][a-z]+, this finding will be SILENTLY DROPPED — a real bug.

    This test asserts the desired behaviour. If it fails, implementation
    has the documented CJK-name regex hole."""
    blocks = [
        ("b" + "0" * 31, "李四在週一交付了 spec。"),
        ("b" + "1" + "0" * 30, "今天和李四討論了 KAS 設計。"),
    ]
    db = _make_db(blocks=blocks, canonical_names=[])
    findings = await check_missing_entities(db, space_id="space-1")
    cjk_caught = any("李四" in (f.message + str(f.metadata)) for f in findings)
    assert cjk_caught, (
        "BUG INDICATOR (CJK regex hole): '李四' appears in 2 blocks with no "
        "canonical entity row. If the extractor's regex only matches "
        "capitalised Latin words, Chinese names are systematically dropped."
    )


@pytest.mark.asyncio
async def test_missing_entities_single_mention_below_threshold_silent():
    """(c) Name mentioned in only 1 block (< 2 threshold) → no finding.

    Mutation guard: if author wrote `>= 1` instead of `>= 2`, this fails
    (every name in any block would be reported)."""
    blocks = [("b" + "0" * 31, "Alice Cooper appears once.")]
    db = _make_db(blocks=blocks, canonical_names=[])
    findings = await check_missing_entities(db, space_id="space-1")
    cooper_findings = [f for f in findings if "Alice" in str(f.metadata)]
    assert cooper_findings == [], (
        "single-mention name must NOT surface as missing_entity. "
        "If this fails, the threshold predicate is `>= 1` instead of `>= 2`."
    )


@pytest.mark.asyncio
async def test_missing_entities_already_canonicalised_silent():
    """(d) Name in 2+ blocks but already exists as canonical entity → no finding.

    Mutation guard: if author forgot to subtract canonicals from candidates,
    this test catches it."""
    blocks = [
        ("b" + "0" * 31, "Alice Cooper rocks."),
        ("b" + "1" + "0" * 30, "Alice Cooper sings."),
    ]
    db = _make_db(blocks=blocks, canonical_names=["Alice Cooper"])
    findings = await check_missing_entities(db, space_id="space-1")
    alice_findings = [f for f in findings if "Alice Cooper" in str(f.metadata)]
    assert alice_findings == [], (
        "BUG INDICATOR: canonicalised entity 'Alice Cooper' should be excluded "
        "from missing_entity findings."
    )
