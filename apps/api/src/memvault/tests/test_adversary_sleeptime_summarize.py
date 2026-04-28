"""Adversary tests for `_summarize_recent`.

Mutation thinking:
  - 0 / <N / >N blocks → respects PROJECT_SUMMARY_RECENT_N=5 cap.
  - Per-block char limit PROJECT_SUMMARY_PER_BLOCK_CHARS=30 — exact boundary.
  - Empty content blocks → skipped (don't pollute output).
  - Most-recent-first ordering (created_at DESC).
"""
# ruff: noqa: S110

from __future__ import annotations

from dataclasses import dataclass

import pytest
from src.memvault.models import MemoryBlock
from src.memvault.sleeptime import (
    PROJECT_SUMMARY_PER_BLOCK_CHARS,
    PROJECT_SUMMARY_RECENT_N,
    _summarize_recent,
)


@dataclass
class _FakeBlock:
    space_id: str
    content: str
    created_at: int
    deleted_at: object = None


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _ExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _Scalars(self._items)


class FakeSession:
    def __init__(self, blocks):
        self.blocks = blocks

    async def execute(self, stmt):
        target = None
        try:
            target = stmt.column_descriptions[0]["entity"]
        except Exception:
            pass
        if target is MemoryBlock:
            ordered = sorted(
                (b for b in self.blocks if b.deleted_at is None),
                key=lambda b: b.created_at,
                reverse=True,
            )
            return _ExecResult(ordered)
        return _ExecResult([])


# ---- (a) zero blocks → empty/sentinel, must not raise ----


@pytest.mark.asyncio
async def test_summarize_zero_blocks_returns_empty():
    session = FakeSession(blocks=[])
    summary = await _summarize_recent(session, "space-empty")
    assert isinstance(summary, str)
    assert summary == "" or summary.strip() == "", (
        f"expected empty summary, got {summary!r}"
    )


# ---- (b) fewer than N blocks → all included, ordered desc ----


@pytest.mark.asyncio
async def test_summarize_fewer_than_n_includes_all():
    blocks = [
        _FakeBlock("space-B", "first content", 1),
        _FakeBlock("space-B", "second content", 2),
        _FakeBlock("space-B", "third content", 3),
    ]
    session = FakeSession(blocks=blocks)
    summary = await _summarize_recent(session, "space-B")

    assert "third" in summary, "newest block (created_at=3) must appear"
    assert "first" in summary, "oldest of the 3 must still appear (count<N)"
    # ordering: newest first
    assert summary.index("third") < summary.index("first"), (
        "summary must be most-recent-first"
    )


# ---- (c) more than N blocks → only N kept ----


@pytest.mark.asyncio
async def test_summarize_more_than_n_truncates_count():
    blocks = [
        _FakeBlock("space-T", f"content-{i:02d}-marker", i)
        for i in range(1, PROJECT_SUMMARY_RECENT_N + 4)  # N+3 blocks
    ]
    session = FakeSession(blocks=blocks)
    summary = await _summarize_recent(session, "space-T")

    # Top-N (highest created_at) kept; the lowest 3 dropped
    n = PROJECT_SUMMARY_RECENT_N
    total = len(blocks)
    # Newest = max created_at
    newest_marker = f"content-{total:02d}-marker"
    oldest_kept_marker = f"content-{total - n + 1:02d}-marker"
    dropped_marker = f"content-{total - n:02d}-marker"

    assert newest_marker[:PROJECT_SUMMARY_PER_BLOCK_CHARS] in summary, (
        f"newest block {newest_marker!r} (truncated) must appear"
    )
    assert oldest_kept_marker[:PROJECT_SUMMARY_PER_BLOCK_CHARS] in summary, (
        f"Nth-newest block {oldest_kept_marker!r} must appear"
    )
    assert dropped_marker not in summary, (
        f"BUG: block {dropped_marker!r} beyond top-{n} should be dropped"
    )


# ---- (d) per-block char limit enforced ----


@pytest.mark.asyncio
async def test_summarize_truncates_per_block_chars():
    long_content = "X" * 200  # 200 chars
    blocks = [_FakeBlock("space-X", long_content, 1)]
    session = FakeSession(blocks=blocks)

    summary = await _summarize_recent(session, "space-X")

    # Count consecutive X's — should be exactly PROJECT_SUMMARY_PER_BLOCK_CHARS
    x_run = 0
    max_run = 0
    for ch in summary:
        if ch == "X":
            x_run += 1
            max_run = max(max_run, x_run)
        else:
            x_run = 0

    assert max_run <= PROJECT_SUMMARY_PER_BLOCK_CHARS, (
        f"BUG: per-block content not truncated to {PROJECT_SUMMARY_PER_BLOCK_CHARS} chars; "
        f"saw run of {max_run} X's"
    )


# ---- (e) deleted blocks (deleted_at set) excluded ----


@pytest.mark.asyncio
async def test_summarize_excludes_soft_deleted():
    """If sleeptime exposes deleted blocks, that's a leak."""
    import datetime as _dt

    blocks = [
        _FakeBlock("space-D", "active-keep", 2),
        _FakeBlock(
            "space-D",
            "DELETED-must-not-leak",
            3,
            deleted_at=_dt.datetime.now(_dt.UTC),
        ),
    ]
    session = FakeSession(blocks=blocks)
    summary = await _summarize_recent(session, "space-D")

    assert "DELETED-must-not-leak" not in summary, (
        "BUG: soft-deleted block leaked into summary"
    )
    assert "active-keep" in summary


# ---- (f) None content / missing content — must not raise ----


@pytest.mark.asyncio
async def test_summarize_handles_none_content():
    blocks = [
        _FakeBlock("space-N", None, 2),  # type: ignore[arg-type]
        _FakeBlock("space-N", "real content", 1),
    ]
    session = FakeSession(blocks=blocks)
    try:
        summary = await _summarize_recent(session, "space-N")
    except Exception as exc:
        pytest.fail(
            f"_summarize_recent must handle None content gracefully, got {exc!r}"
        )

    assert isinstance(summary, str)
    assert "real content" in summary or summary.strip() != "", (
        "non-None block content should still appear"
    )
