"""Adversary tests for `_wire_capture_subscription`.

Mutation thinking:
  - event_bus exposes `.channel(name).subscribe_handler(...)` API → use it.
  - event_bus has no `.channel` (or it raises) but has `.subscribe(...)` →
    use fallback subscribe.
  - event_bus has neither / both raise → silent no-op (must not raise).

Invariant: _wire_capture_subscription must NEVER raise.
"""

from __future__ import annotations

import sys

import pytest
from src.memvault.sleeptime import (
    _wire_capture_subscription,
)


def _patch_bus(monkeypatch, bus):
    """Replace event_bus in src.events.bus stub with a custom object."""
    bus_mod = sys.modules["src.events.bus"]
    monkeypatch.setattr(bus_mod, "event_bus", bus, raising=False)


# ---- (a) channel API present → channel().subscribe_handler called ----


class _ChannelBus:
    def __init__(self):
        self.channel_calls: list[str] = []
        self.handlers: list = []

    def channel(self, name):
        self.channel_calls.append(name)
        outer = self

        class _Ch:
            def subscribe_handler(self, handler):
                outer.handlers.append(handler)
                return None

        return _Ch()


def test_wire_uses_channel_api(monkeypatch):
    bus = _ChannelBus()
    _patch_bus(monkeypatch, bus)

    try:
        _wire_capture_subscription()
    except Exception as exc:
        pytest.fail(f"wire must not raise on channel-API bus, got {exc!r}")

    # at least one channel registration with capture event name
    assert bus.channel_calls, "BUG: channel API not used when available"
    assert any("capture" in c for c in bus.channel_calls), (
        f"channel name should be capture-related, got {bus.channel_calls}"
    )
    assert bus.handlers, "no handler subscribed via channel API"


# ---- (b) channel API missing, fallback `.subscribe()` available ----


class _SubscribeBus:
    def __init__(self):
        self.subscribed: list[tuple[str, object]] = []
        # No `channel` attribute at all

    def subscribe(self, name, handler):
        self.subscribed.append((name, handler))


def test_wire_falls_back_to_subscribe(monkeypatch):
    bus = _SubscribeBus()
    _patch_bus(monkeypatch, bus)

    try:
        _wire_capture_subscription()
    except Exception as exc:
        pytest.fail(f"wire must not raise on subscribe-only bus, got {exc!r}")

    assert bus.subscribed, (
        "BUG: fallback subscribe() not called when channel() unavailable"
    )
    name, _h = bus.subscribed[0]
    assert "capture" in name, f"subscribed event name should be capture-related, got {name!r}"


# ---- (c) channel raises, subscribe also raises → silent no-op ----


class _BrokenBus:
    def channel(self, name):
        raise RuntimeError("channel broken")

    def subscribe(self, *_a, **_kw):
        raise RuntimeError("subscribe broken")


def test_wire_silent_when_bus_broken(monkeypatch):
    bus = _BrokenBus()
    _patch_bus(monkeypatch, bus)

    try:
        _wire_capture_subscription()
    except Exception as exc:
        pytest.fail(
            f"BUG: wire must silently no-op on broken bus, got {type(exc).__name__}: {exc}"
        )


# ---- (d) bus is None → must not raise ----


def test_wire_silent_when_bus_is_none(monkeypatch):
    _patch_bus(monkeypatch, None)
    try:
        _wire_capture_subscription()
    except Exception as exc:
        pytest.fail(f"BUG: wire raised on None bus, got {exc!r}")


# ---- (e) bus has neither channel nor subscribe — must not raise ----


class _BareBus:
    pass


def test_wire_silent_when_bus_bare(monkeypatch):
    _patch_bus(monkeypatch, _BareBus())
    try:
        _wire_capture_subscription()
    except Exception as exc:
        pytest.fail(
            f"BUG: wire raised on bus with no methods, got {type(exc).__name__}: {exc}"
        )
