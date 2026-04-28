"""Adversary tests for `_run_sleeptime` resilience.

Mutation thinking:
  - 3 layers of failure (lint / DB / event_bus.publish) should each be isolated.
  - Invariant: _run_sleeptime always returns a dict; never raises.
  - Happy path: 3 block_types all materialised (persona/human/project).
"""
# ruff: noqa: S110

from __future__ import annotations

from dataclasses import dataclass

import pytest
from src.memvault import sleeptime as sleeptime_mod
from src.memvault.models import MemoryBlock, MemoryBlockSnapshot
from src.memvault.sleeptime import (
    _run_sleeptime,
)


@dataclass
class _FakeBlock:
    space_id: str
    content: str
    created_at: int
    deleted_at: object = None


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class _ExecResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarsResult(self._items)

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class FakeSession:
    def __init__(self, recent_blocks=None):
        self.recent_blocks = recent_blocks or []
        self.snapshots: dict[tuple[str, str], MemoryBlockSnapshot] = {}
        self.committed = False
        self._next_lookup_hint = None
        self.commit_raises = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt):
        target = None
        try:
            target = stmt.column_descriptions[0]["entity"]
        except Exception:
            pass

        if target is MemoryBlockSnapshot:
            items = list(self.snapshots.values())
            hint = self._next_lookup_hint
            if hint is not None:
                items = [
                    s
                    for s in self.snapshots.values()
                    if (s.space_id, s.block_type) == hint
                ]
            return _ExecResult(items)

        if target is MemoryBlock:
            ordered = sorted(
                (b for b in self.recent_blocks if b.deleted_at is None),
                key=lambda b: b.created_at,
                reverse=True,
            )
            return _ExecResult(ordered)

        return _ExecResult([])

    def add(self, obj):
        if isinstance(obj, MemoryBlockSnapshot):
            self.snapshots[(obj.space_id, obj.block_type)] = obj

    async def commit(self):
        if self.commit_raises:
            raise RuntimeError("simulated DB commit failure")
        self.committed = True


def _hint_aware(monkeypatch):
    """Patch _ensure_block to set _next_lookup_hint before each call so the
    FakeSession returns the right (space_id, block_type) row."""
    real_ensure = sleeptime_mod._ensure_block

    async def hinted(db, space_id, block_type, content):
        db._next_lookup_hint = (space_id, block_type)
        return await real_ensure(db, space_id, block_type, content)

    monkeypatch.setattr(sleeptime_mod, "_ensure_block", hinted)


# ---------------------------------------------------------------------------
# (a) lint raises → _run_sleeptime still completes, returns dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_lint_failure_isolated(monkeypatch):
    session = FakeSession(
        recent_blocks=[_FakeBlock("space-L", "lint-failure-test", 1)]
    )
    monkeypatch.setattr(sleeptime_mod, "async_session_factory", lambda: session)
    _hint_aware(monkeypatch)

    async def boom_health(_space_id):
        raise RuntimeError("lint exploded")

    monkeypatch.setattr(sleeptime_mod, "_safe_health_check", boom_health)

    async def fake_emit(**_kw):
        return None

    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", fake_emit)

    try:
        result = await _run_sleeptime("space-L")
    except Exception as exc:
        pytest.fail(f"_run_sleeptime must isolate lint failure, got {exc!r}")

    assert isinstance(result, dict), "invariant: always returns dict"
    assert result.get("space_id") == "space-L"


# ---------------------------------------------------------------------------
# (b) DB factory raises → still returns dict, no propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_db_factory_failure(monkeypatch):
    def boom():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(sleeptime_mod, "async_session_factory", boom)

    try:
        result = await _run_sleeptime("space-DB")
    except Exception as exc:
        pytest.fail(f"_run_sleeptime must swallow DB outages, got {exc!r}")

    assert isinstance(result, dict)
    assert result.get("space_id") == "space-DB"
    assert result.get("blocks_updated") == [], "no blocks updated on DB outage"


# ---------------------------------------------------------------------------
# (c) commit() raises mid-flight → still returns dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_commit_failure(monkeypatch):
    session = FakeSession(
        recent_blocks=[_FakeBlock("space-C", "commit-test", 1)]
    )
    session.commit_raises = True
    monkeypatch.setattr(sleeptime_mod, "async_session_factory", lambda: session)
    _hint_aware(monkeypatch)

    async def fake_emit(**_kw):
        return None

    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", fake_emit)

    try:
        result = await _run_sleeptime("space-C")
    except Exception as exc:
        pytest.fail(f"_run_sleeptime must swallow commit failures, got {exc!r}")

    assert isinstance(result, dict)
    assert result.get("space_id") == "space-C"


# ---------------------------------------------------------------------------
# (d) event_bus.publish raises → return dict still correct
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_emit_failure_isolated(monkeypatch):
    session = FakeSession(
        recent_blocks=[_FakeBlock("space-E", "emit-failure", 1)]
    )
    monkeypatch.setattr(sleeptime_mod, "async_session_factory", lambda: session)
    _hint_aware(monkeypatch)

    async def boom_emit(**_kw):
        raise RuntimeError("event bus failed")

    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", boom_emit)

    try:
        result = await _run_sleeptime("space-E")
    except Exception as exc:
        pytest.fail(f"_run_sleeptime must swallow emit failure, got {exc!r}")

    assert isinstance(result, dict)
    assert result.get("space_id") == "space-E"
    # blocks_updated should still reflect what was written before emit
    assert "blocks_updated" in result


# ---------------------------------------------------------------------------
# (e) Happy path — three block_types all materialised, only project has content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_happy_path_three_blocks(monkeypatch):
    session = FakeSession(
        recent_blocks=[
            _FakeBlock("space-H", "Recent capture about sleeptime work", 5),
            _FakeBlock("space-H", "Earlier note on memvault refactor", 4),
        ]
    )
    monkeypatch.setattr(sleeptime_mod, "async_session_factory", lambda: session)
    _hint_aware(monkeypatch)

    async def fake_emit(**_kw):
        return None

    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", fake_emit)

    result = await _run_sleeptime("space-H")
    assert isinstance(result, dict)

    types_present = {bt for (_, bt) in session.snapshots.keys()}
    assert types_present == {"persona", "human", "project"}, (
        f"all 3 block_types must be ensured, got {types_present}"
    )
    project = session.snapshots[("space-H", "project")]
    persona = session.snapshots[("space-H", "persona")]
    human = session.snapshots[("space-H", "human")]
    assert project.content, "project must have content"
    assert persona.content is None, "persona is placeholder (W4)"
    assert human.content is None, "human is placeholder (W4)"
    assert session.committed is True


# ---------------------------------------------------------------------------
# (f) Invariant: return is always dict (sweep multiple failure shapes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sleeptime_always_returns_dict_invariant(monkeypatch):
    """Sweep: lint raise + factory raise + emit raise simultaneously.
    Must still return dict, not None or raise."""

    def boom_factory():
        raise RuntimeError("DB dead")

    async def boom_health(_):
        raise RuntimeError("lint dead")

    async def boom_emit(**_kw):
        raise RuntimeError("emit dead")

    monkeypatch.setattr(sleeptime_mod, "async_session_factory", boom_factory)
    monkeypatch.setattr(sleeptime_mod, "_safe_health_check", boom_health)
    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", boom_emit)

    result = await _run_sleeptime("space-everything-burns")
    assert isinstance(result, dict), (
        "BUG: _run_sleeptime returned non-dict on triple failure"
    )
    assert result.get("space_id") == "space-everything-burns"
