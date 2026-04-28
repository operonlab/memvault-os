"""Store middleware — intercept dispatch lifecycle.

Adapted from pystorex middleware pattern. Three lifecycle hooks:
- before_dispatch(action, state) — pre-processing, can modify action
- after_dispatch(action, old_state, new_state) — post-success
- on_error(action, state, error) — post-failure

Middlewares compose in order: first registered = outermost wrapper.

Usage:
    from src.shared.middleware import LoggerMiddleware, AuditMiddleware
    from src.shared.store import FeatureStore

    store = FeatureStore(
        "finance",
        finance_reducer,
        middlewares=[LoggerMiddleware(), AuditMiddleware({"finance.wallet.created"})],
    )

    # Or add dynamically:
    store.use(AuditMiddleware({"finance.transaction.created"}))
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from src.shared.actions import Action

logger = logging.getLogger(__name__)


# ── Base ─────────────────────────────────────────────────────────────────


class StoreMiddleware:
    """Base class for store middleware. Override hooks as needed."""

    async def before_dispatch(self, action: Action, state: Any) -> Action:
        """Called before reducer. Can modify/replace the action, or raise to abort."""
        return action

    async def after_dispatch(self, action: Action, old_state: Any, new_state: Any) -> None:
        """Called after successful reducer + state update."""

    async def on_error(self, action: Action, state: Any, error: Exception) -> None:
        """Called when dispatch fails."""


# ── ThrottledError ────────────────────────────────────────────────────────


class ThrottledError(Exception):
    """Raised by ThrottleMiddleware when an action is dispatched too rapidly."""

    def __init__(self, action_type: str, interval_seconds: float) -> None:
        self.action_type = action_type
        self.interval_seconds = interval_seconds
        super().__init__(
            f"Action '{action_type}' throttled — interval {interval_seconds}s not elapsed"
        )


# ── 1. LoggerMiddleware ───────────────────────────────────────────────────


class LoggerMiddleware(StoreMiddleware):
    """Log all dispatches — action type + duration + state change."""

    def __init__(self, logger_name: str = "store") -> None:
        self._logger = logging.getLogger(logger_name)
        self._start: float = 0.0

    async def before_dispatch(self, action: Action, state: Any) -> Action:
        self._start = time.monotonic()
        return action

    async def after_dispatch(self, action: Action, old_state: Any, new_state: Any) -> None:
        elapsed = (time.monotonic() - self._start) * 1000
        changed = old_state is not new_state
        self._logger.info("dispatch %s (%.1fms, changed=%s)", action.type, elapsed, changed)

    async def on_error(self, action: Action, state: Any, error: Exception) -> None:
        elapsed = (time.monotonic() - self._start) * 1000
        self._logger.error("dispatch %s failed (%.1fms): %s", action.type, elapsed, error)


# ── 2. PerformanceMiddleware ──────────────────────────────────────────────


class PerformanceMiddleware(StoreMiddleware):
    """Track dispatch performance, warn on slow dispatches."""

    def __init__(self, warn_threshold_ms: float = 100.0) -> None:
        self._warn_threshold_ms = warn_threshold_ms
        self._start: float = 0.0
        self._current_action_type: str = ""
        # action_type → list of durations in ms
        self._durations: dict[str, list[float]] = defaultdict(list)

    async def before_dispatch(self, action: Action, state: Any) -> Action:
        self._start = time.monotonic()
        self._current_action_type = action.type
        return action

    async def after_dispatch(self, action: Action, old_state: Any, new_state: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        self._durations[action.type].append(elapsed_ms)
        if elapsed_ms > self._warn_threshold_ms:
            logging.getLogger("store.perf").warning(
                "Slow dispatch: %s took %.1fms (threshold %.1fms)",
                action.type,
                elapsed_ms,
                self._warn_threshold_ms,
            )

    def get_stats(self) -> dict[str, dict[str, float]]:
        """Return timing stats per action type: count, avg_ms, max_ms, p95_ms."""
        result: dict[str, dict[str, float]] = {}
        for action_type, durations in self._durations.items():
            if not durations:
                continue
            sorted_d = sorted(durations)
            count = len(sorted_d)
            avg_ms = sum(sorted_d) / count
            max_ms = sorted_d[-1]
            p95_idx = max(0, int(count * 0.95) - 1)
            p95_ms = sorted_d[p95_idx]
            result[action_type] = {
                "count": float(count),
                "avg_ms": avg_ms,
                "max_ms": max_ms,
                "p95_ms": p95_ms,
            }
        return result


# ── 3. ThrottleMiddleware ─────────────────────────────────────────────────


class ThrottleMiddleware(StoreMiddleware):
    """Throttle rapid dispatches of same action type.

    If the same action type is dispatched within `interval_seconds`,
    a ThrottledError is raised. Use action_types=None to throttle ALL types.
    """

    def __init__(
        self,
        interval_seconds: float = 1.0,
        action_types: set[str] | None = None,
    ) -> None:
        self._interval = interval_seconds
        self._action_types = action_types  # None = throttle all
        # action_type → last dispatch monotonic time
        self._last_dispatch: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def before_dispatch(self, action: Action, state: Any) -> Action:
        if self._action_types is not None and action.type not in self._action_types:
            return action  # not a throttled type

        async with self._lock:
            last = self._last_dispatch.get(action.type)
            now = time.monotonic()
            if last is not None and (now - last) < self._interval:
                raise ThrottledError(action.type, self._interval)
            self._last_dispatch[action.type] = now
        return action


# ── 4. ErrorMiddleware ────────────────────────────────────────────────────


class ErrorMiddleware(StoreMiddleware):
    """Global error boundary for dispatch failures.

    Logs all dispatch errors and optionally calls an async callback.
    The callback signature: async (action, error) -> None
    """

    def __init__(self, on_error_callback=None) -> None:
        self._callback = on_error_callback

    async def on_error(self, action: Action, state: Any, error: Exception) -> None:
        logging.getLogger("store.error").error("dispatch failed: %s — %s", action.type, error)
        if self._callback is not None:
            import inspect

            result = self._callback(action, error)
            if inspect.isawaitable(result):
                await result


# ── 5. AuditMiddleware ────────────────────────────────────────────────────


class AuditMiddleware(StoreMiddleware):
    """Record audit trail for sensitive action types."""

    def __init__(self, audit_types: set[str]) -> None:
        self._audit_types = audit_types
        self._trail: list[dict] = []

    async def after_dispatch(self, action: Action, old_state: Any, new_state: Any) -> None:
        if action.type in self._audit_types:
            self._trail.append(
                {
                    "type": action.type,
                    "payload": action.payload,
                    "timestamp": time.time(),
                    "state_changed": old_state is not new_state,
                }
            )

    def get_trail(self) -> list[dict]:
        """Return a copy of the audit trail."""
        return list(self._trail)

    def clear(self) -> None:
        """Clear the audit trail (e.g., after persisting to DB)."""
        self._trail.clear()


# ── 6. BatchMiddleware ────────────────────────────────────────────────────


class BatchMiddleware(StoreMiddleware):
    """Batch rapid dispatches — collect and flush.

    Accumulates actions in a buffer. Call flush() to retrieve and clear
    the buffered actions. Useful for invest module (rapid price updates)
    where callers want to process a set of updates together.

    action_types=None means buffer ALL action types.
    """

    def __init__(
        self,
        window_ms: float = 50.0,
        action_types: set[str] | None = None,
    ) -> None:
        self._window_ms = window_ms
        self._action_types = action_types
        self._buffer: list[Action] = []

    async def before_dispatch(self, action: Action, state: Any) -> Action:
        if self._action_types is None or action.type in self._action_types:
            self._buffer.append(action)
        return action

    def flush(self) -> list[Action]:
        """Return and clear the accumulated action buffer."""
        batch = list(self._buffer)
        self._buffer.clear()
        return batch

    def pending_count(self) -> int:
        """Number of actions currently in the buffer."""
        return len(self._buffer)
