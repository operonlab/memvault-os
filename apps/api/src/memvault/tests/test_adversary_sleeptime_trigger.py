"""Adversary tests for `maybe_trigger_sleeptime`.

Mutation-thinking surface:
  - count % SLEEPTIME_INTERVAL == 0 — would `!=` survive? boundary count=0/negative.
  - space_id falsy edge cases (empty / None / whitespace).
  - Real `asyncio.ensure_future` scheduling — verify task lands in
    sleeptime._background_tasks (without mocking ensure_future itself).
  - Invariant: maybe_trigger_sleeptime must NEVER raise (fire-and-forget gate).
"""
# ruff: noqa: F841

from __future__ import annotations

import asyncio

import pytest
from src.memvault import sleeptime as sleeptime_mod
from src.memvault.sleeptime import (
    SLEEPTIME_INTERVAL,
    maybe_trigger_sleeptime,
)


@pytest.fixture(autouse=True)
def _quiet_run_sleeptime(monkeypatch):
    """Replace _run_sleeptime with a non-DB-touching async stub. Tests that need
    to observe scheduling override this with their own."""
    fired: list[str] = []

    async def fake_run(space_id):
        fired.append(space_id)

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", fake_run)
    sleeptime_mod._background_tasks.clear()
    yield fired
    sleeptime_mod._background_tasks.clear()


# ---- (a) interval boundary: count == SLEEPTIME_INTERVAL fires + task scheduled ----


@pytest.mark.asyncio
async def test_trigger_at_interval_boundary_schedules_task(_quiet_run_sleeptime):
    fired = _quiet_run_sleeptime
    assert sleeptime_mod._background_tasks == set()

    triggered = await maybe_trigger_sleeptime("space-A", SLEEPTIME_INTERVAL)

    assert triggered is True, "must return True at exact interval"
    # task should be ensure_future'd; might already complete after sleep(0)
    # but if not, it should still be tracked while in-flight.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fired == ["space-A"], "background task must have actually run"


# ---- (b) off-interval count must NOT fire ----


@pytest.mark.asyncio
async def test_trigger_off_interval_returns_false(_quiet_run_sleeptime):
    fired = _quiet_run_sleeptime

    res = await maybe_trigger_sleeptime("space-B", SLEEPTIME_INTERVAL + 1)
    await asyncio.sleep(0)

    assert res is False
    assert fired == []


# ---- (c) count == 0 must NOT fire (mutation guard against `% N == 0` accepting 0) ----


@pytest.mark.asyncio
async def test_trigger_count_zero_does_not_fire(_quiet_run_sleeptime):
    """BUG indicator: pure `count % 5 == 0` would accept count=0 and fire.
    A correct guard requires `count > 0 and count % N == 0`."""
    fired = _quiet_run_sleeptime

    triggered = await maybe_trigger_sleeptime("space-C", 0)
    await asyncio.sleep(0)

    assert triggered is False, "count=0 must NOT trigger sleeptime"
    assert fired == [], "no background work should be scheduled at count=0"


# ---- (d) negative count must NOT fire ----


@pytest.mark.asyncio
async def test_trigger_negative_count_does_not_fire(_quiet_run_sleeptime):
    fired = _quiet_run_sleeptime

    for c in [-1, -5, -10]:
        triggered = await maybe_trigger_sleeptime("space-D", c)
        assert triggered is False, f"count={c} must not trigger"

    await asyncio.sleep(0)
    assert fired == []


# ---- (e) empty space_id must short-circuit ----


@pytest.mark.asyncio
async def test_trigger_empty_space_id_returns_false(_quiet_run_sleeptime):
    fired = _quiet_run_sleeptime

    triggered = await maybe_trigger_sleeptime("", SLEEPTIME_INTERVAL)
    await asyncio.sleep(0)

    assert triggered is False
    assert fired == []


# ---- (f) None space_id — invariant: must not raise ----


@pytest.mark.asyncio
async def test_trigger_none_space_id_does_not_raise(_quiet_run_sleeptime):
    """Invariant: maybe_trigger_sleeptime is fire-and-forget; it must never raise.
    Acceptable: returns False OR returns True but logs. Not acceptable: TypeError.
    """
    fired = _quiet_run_sleeptime
    try:
        result = await maybe_trigger_sleeptime(None, SLEEPTIME_INTERVAL)  # type: ignore[arg-type]
    except Exception as exc:
        pytest.fail(f"trigger must not raise on None space_id, got {type(exc).__name__}: {exc}")

    # bool invariant
    assert isinstance(result, bool), "must return bool even on bad input"
    await asyncio.sleep(0)
    # If it returned True, the fake_run will have appended None — flag as bug
    if result is True:
        # Spec ambiguous; surface as warning via assertion to be reviewed
        assert fired == [None], "if trigger fires on None, fake_run should record None"


# ---- (g) whitespace-only space_id — should be treated as falsy or pass-through? ----


@pytest.mark.asyncio
async def test_trigger_whitespace_space_id_behavior(_quiet_run_sleeptime):
    """Documents behavior — whitespace is non-empty so likely passes guard.
    Not asserting bug, just pinning current behavior."""
    fired = _quiet_run_sleeptime
    result = await maybe_trigger_sleeptime("   ", SLEEPTIME_INTERVAL)
    assert isinstance(result, bool)
    await asyncio.sleep(0)


# ---- (h) ensure_future invariant: task tracked in _background_tasks set ----


@pytest.mark.asyncio
async def test_trigger_tracks_task_in_background_set(monkeypatch):
    """Invariant: spawned task must be added to _background_tasks (so GC doesn't
    drop it mid-flight). Use a slow fake_run so we observe the task while live."""
    sleeptime_mod._background_tasks.clear()
    seen_sizes: list[int] = []

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_run(space_id):
        started.set()
        await release.wait()

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", slow_run)

    triggered = await maybe_trigger_sleeptime("space-track", SLEEPTIME_INTERVAL)
    assert triggered is True

    # Yield until the task starts
    for _ in range(5):
        if started.is_set():
            break
        await asyncio.sleep(0)
    seen_sizes.append(len(sleeptime_mod._background_tasks))

    release.set()
    # Drain tasks
    for _ in range(10):
        await asyncio.sleep(0)
        if not sleeptime_mod._background_tasks:
            break

    assert seen_sizes[0] >= 1, (
        "background task must be tracked in _background_tasks while running "
        f"(got size={seen_sizes[0]})"
    )
    assert sleeptime_mod._background_tasks == set(), (
        "background task should be discarded from set after completion"
    )


# ---- (i) trigger isolates async _run_sleeptime exceptions during await loop ----


@pytest.mark.asyncio
async def test_trigger_isolates_async_run_sleeptime_exception(monkeypatch):
    """Invariant: even if the awaited _run_sleeptime raises inside the task,
    the parent must not see the exception (fire-and-forget). The exception
    should at most surface as an unhandled-task warning later."""

    async def bad_run(space_id):
        raise RuntimeError("internal error inside _run_sleeptime")

    monkeypatch.setattr(sleeptime_mod, "_run_sleeptime", bad_run)

    try:
        result = await maybe_trigger_sleeptime("space-bad", SLEEPTIME_INTERVAL)
    except Exception as exc:
        pytest.fail(
            f"trigger must isolate awaited _run_sleeptime errors, got {exc!r}"
        )
    assert isinstance(result, bool)
    # let the failing task complete so it doesn't pollute next test
    await asyncio.sleep(0)
    await asyncio.sleep(0)
