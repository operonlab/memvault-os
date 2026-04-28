"""Unit tests for memvault sleeptime reflection agent (Worker 4).

Validates:
  - Trigger interval alignment (count=N → fire; N+1, N+2 → noop)
  - _run_sleeptime upserts 3 rows (persona/human/project), only project has content
  - _summarize_recent placeholder behavior
  - lint health-check fallback (worker 3 not yet landed)
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Stub `src.*` imports so sleeptime.py can be imported without full Workshop env.
# Pytest collection imports the test module before fixtures run, so stubs must
# be installed at module import time.
# ---------------------------------------------------------------------------


def _install_stubs() -> None:
    if "src" in sys.modules and getattr(sys.modules["src"], "__sleeptime_stub__", False):
        return

    src_pkg = types.ModuleType("src")
    src_pkg.__sleeptime_stub__ = True
    src_pkg.__path__ = []  # type: ignore[attr-defined]

    shared_pkg = types.ModuleType("src.shared")
    shared_pkg.__path__ = []  # type: ignore[attr-defined]

    db_mod = types.ModuleType("src.shared.database")

    class _StubSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *_a, **_kw):  # pragma: no cover - replaced by fake
            raise RuntimeError("stub session not wired")

        def add(self, *_a, **_kw):
            pass

        async def commit(self):
            pass

    def _factory():
        return _StubSession()

    db_mod.async_session_factory = _factory

    cache_mod = types.ModuleType("src.shared.cache")

    def _get_redis():
        return None

    cache_mod.get_redis = _get_redis

    models_pkg = types.ModuleType("src.shared.models")

    class _Base:
        pass

    class _SpaceScopedModel(_Base):
        pass

    models_pkg.Base = _Base
    models_pkg.SpaceScopedModel = _SpaceScopedModel

    events_pkg = types.ModuleType("src.events")
    events_pkg.__path__ = []  # type: ignore[attr-defined]

    bus_mod = types.ModuleType("src.events.bus")

    class _Bus:
        async def publish(self, *_a, **_kw):
            return None

        def channel(self, _name):
            class _Ch:
                def subscribe_handler(self, _h):
                    return None

            return _Ch()

    bus_mod.event_bus = _Bus()

    sys.modules["src"] = src_pkg
    sys.modules["src.shared"] = shared_pkg
    sys.modules["src.shared.database"] = db_mod
    sys.modules["src.shared.cache"] = cache_mod
    sys.modules["src.shared.models"] = models_pkg
    sys.modules["src.events"] = events_pkg
    sys.modules["src.events.bus"] = bus_mod


_install_stubs()

# Make memvault module importable as `memvault_pkg` without importing siblings
# (events.py / dream.py would pull in heavy deps).
_HERE = Path(__file__).resolve().parent.parent
_MODULE_PATH = _HERE / "sleeptime.py"
_MODELS_PATH = _HERE / "models.py"

# Import sleeptime as a standalone module — bypass package __init__.
import importlib.util


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Build a minimal package shell to host both modules with relative imports.
_PKG_NAME = "memvault_pkg"
if _PKG_NAME not in sys.modules:
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_HERE)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg

# Stub a no-op lint module so sleeptime's "from . import lint" succeeds without
# loading the real one (which depends on more memvault internals).
if f"{_PKG_NAME}.lint" not in sys.modules:
    stub_lint = types.ModuleType(f"{_PKG_NAME}.lint")
    sys.modules[f"{_PKG_NAME}.lint"] = stub_lint
    setattr(sys.modules[_PKG_NAME], "lint", stub_lint)

models_mod = _load_module(f"{_PKG_NAME}.models", _MODELS_PATH)
sleeptime_mod = _load_module(f"{_PKG_NAME}.sleeptime", _MODULE_PATH)

MemoryBlockSnapshot = models_mod.MemoryBlockSnapshot
MemoryBlock = models_mod.MemoryBlock
maybe_trigger_sleeptime = sleeptime_mod.maybe_trigger_sleeptime
_run_sleeptime = sleeptime_mod._run_sleeptime
_summarize_recent = sleeptime_mod._summarize_recent
_ensure_block = sleeptime_mod._ensure_block


# ---------------------------------------------------------------------------
# Fakes for AsyncSession behaviour
# ---------------------------------------------------------------------------


@dataclass
class _FakeBlock:
    """Mimics MemoryBlock just enough for _summarize_recent."""

    space_id: str
    content: str
    created_at: int  # ordering only
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
    """In-memory async session — supports the few patterns sleeptime uses."""

    def __init__(self, recent_blocks: list[_FakeBlock] | None = None):
        self.recent_blocks = recent_blocks or []
        self.snapshots: dict[tuple[str, str], MemoryBlockSnapshot] = {}
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, stmt):
        # Inspect the SQL-like statement to decide which entity is targeted.
        target = None
        try:
            target = stmt.column_descriptions[0]["entity"]
        except Exception:
            pass

        if target is MemoryBlockSnapshot:
            # naive: just return all matching snapshots; sleeptime calls
            # scalar_one_or_none() so 0/1 row is fine.
            # Filter using WHERE-ish heuristic: caller passes space_id+block_type
            # but we don't parse — return any snapshot whose key matches the
            # most-recent inserted criteria. Instead: just return all and let
            # sleeptime treat list as "one or none".
            items = list(self.snapshots.values())
            # In sleeptime.py we filter by space_id + block_type before this call.
            # We approximate by returning at most one — pick by looking at compile?
            # Simpler: store a hint on the session.
            hint = getattr(self, "_next_lookup_hint", None)
            if hint is not None:
                items = [
                    s
                    for s in self.snapshots.values()
                    if (s.space_id, s.block_type) == hint
                ]
            return _ExecResult(items)

        if target is MemoryBlock:
            # Sort by created_at desc (numeric sort fine for fakes)
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
        self.committed = True


# ---------------------------------------------------------------------------
# Trigger interval tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_fires_on_interval(monkeypatch):
    """count=5 (== SLEEPTIME_INTERVAL) must fire."""
    fired: list[str] = []

    async def fake_run(space_id):
        fired.append(space_id)

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", fake_run)

    triggered = await maybe_trigger_sleeptime("space-A", 5)
    # let scheduled task run
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert triggered is True
    assert fired == ["space-A"]


@pytest.mark.asyncio
async def test_trigger_skips_off_interval(monkeypatch):
    """count=6 and count=7 must NOT fire (not aligned with 5)."""
    fired: list[str] = []

    async def fake_run(space_id):
        fired.append(space_id)

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", fake_run)

    t6 = await maybe_trigger_sleeptime("space-B", 6)
    t7 = await maybe_trigger_sleeptime("space-B", 7)
    await asyncio.sleep(0)

    assert t6 is False
    assert t7 is False
    assert fired == []


@pytest.mark.asyncio
async def test_trigger_zero_or_negative_skips(monkeypatch):
    fired: list[str] = []

    async def fake_run(space_id):
        fired.append(space_id)

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", fake_run)

    assert await maybe_trigger_sleeptime("space-C", 0) is False
    assert await maybe_trigger_sleeptime("space-C", -1) is False
    assert await maybe_trigger_sleeptime("", 5) is False
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_trigger_fires_at_each_multiple(monkeypatch):
    """count=10, 15 must also fire (every multiple of 5)."""
    fired: list[int] = []

    async def fake_run(space_id):
        fired.append(len(fired) + 1)

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", fake_run)

    for c in [5, 6, 7, 8, 9, 10, 11, 14, 15]:
        await maybe_trigger_sleeptime("space-D", c)

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # 5, 10, 15 → 3 fires
    assert len(fired) == 3


# ---------------------------------------------------------------------------
# _ensure_block + _run_sleeptime tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_block_inserts_when_missing():
    session = FakeSession()
    space_id = "space-X"

    session._next_lookup_hint = (space_id, "project")  # type: ignore[attr-defined]
    block = await _ensure_block(session, space_id, "project", "hello world")

    assert block.space_id == space_id
    assert block.block_type == "project"
    assert block.content == "hello world"
    assert block.word_count == 2
    assert block.block_version == 1
    assert (space_id, "project") in session.snapshots


@pytest.mark.asyncio
async def test_ensure_block_bumps_version_on_change():
    session = FakeSession()
    space_id = "space-Y"

    session._next_lookup_hint = (space_id, "project")  # type: ignore[attr-defined]
    first = await _ensure_block(session, space_id, "project", "first content")
    assert first.block_version == 1

    session._next_lookup_hint = (space_id, "project")  # type: ignore[attr-defined]
    second = await _ensure_block(session, space_id, "project", "second content updated")
    assert second is first  # same row mutated
    assert second.content == "second content updated"
    assert second.block_version == 2
    assert second.word_count == 3


@pytest.mark.asyncio
async def test_ensure_block_no_bump_on_identical_content():
    session = FakeSession()
    space_id = "space-Z"

    session._next_lookup_hint = (space_id, "project")  # type: ignore[attr-defined]
    first = await _ensure_block(session, space_id, "project", "same")

    session._next_lookup_hint = (space_id, "project")  # type: ignore[attr-defined]
    second = await _ensure_block(session, space_id, "project", "same")

    assert second is first
    assert second.block_version == 1


@pytest.mark.asyncio
async def test_summarize_recent_concats_first_chars():
    session = FakeSession(
        recent_blocks=[
            _FakeBlock("space-S", "Alpha block content goes here", 3),
            _FakeBlock("space-S", "Beta block second item", 2),
            _FakeBlock("space-S", "", 1),  # empty -> skipped
        ]
    )

    summary = await _summarize_recent(session, "space-S")

    # Most-recent (created_at=3) first
    assert summary.startswith("Alpha block content goes here"[:30])
    assert "|" in summary
    assert "Beta" in summary


@pytest.mark.asyncio
async def test_summarize_recent_empty_returns_empty():
    session = FakeSession(recent_blocks=[])
    summary = await _summarize_recent(session, "space-empty")
    assert summary == ""


@pytest.mark.asyncio
async def test_run_sleeptime_creates_three_blocks(monkeypatch):
    """Full _run_sleeptime: persona/human/project rows materialised; only
    project has content."""
    session = FakeSession(
        recent_blocks=[
            _FakeBlock("space-R", "Recent capture about sleeptime work", 5),
            _FakeBlock("space-R", "Earlier note on memvault refactor", 4),
        ]
    )

    # Patch session factory to yield our fake
    monkeypatch.setattr(
        sleeptime_mod, "async_session_factory", lambda: session
    )

    # Patch lint.run_health_check absence — falls through to []
    # (already absent in the stub lint module)

    # Patch event emit to a no-op so we don't hit stub bus
    async def fake_emit(**_kw):
        return None

    monkeypatch.setattr(sleeptime_mod, "_emit_sleeptime_completed", fake_emit)

    # FakeSession.execute uses _next_lookup_hint to filter snapshots; set hint
    # before each lookup. We monkeypatch _ensure_block to seed the hint.
    real_ensure = sleeptime_mod._ensure_block

    async def hinted_ensure(db, space_id, block_type, content):
        db._next_lookup_hint = (space_id, block_type)
        return await real_ensure(db, space_id, block_type, content)

    monkeypatch.setattr(sleeptime_mod, "_ensure_block", hinted_ensure)

    result = await _run_sleeptime("space-R")

    assert result["space_id"] == "space-R"
    assert "project" in result["blocks_updated"]

    # 3 rows exist
    types_present = {bt for (_, bt) in session.snapshots.keys()}
    assert types_present == {"persona", "human", "project"}

    project = session.snapshots[("space-R", "project")]
    persona = session.snapshots[("space-R", "persona")]
    human = session.snapshots[("space-R", "human")]

    assert project.content
    assert "Recent capture" in (project.content or "")
    # persona / human are placeholders — content None
    assert persona.content is None
    assert human.content is None
    assert session.committed is True


@pytest.mark.asyncio
async def test_run_sleeptime_resilient_on_failure(monkeypatch):
    """Any internal error must not propagate — sleeptime is fire-and-forget."""

    def boom():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(sleeptime_mod, "async_session_factory", boom)

    # Should not raise
    result = await _run_sleeptime("space-fail")
    assert result["space_id"] == "space-fail"
    assert result["blocks_updated"] == []
