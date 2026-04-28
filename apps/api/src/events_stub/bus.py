"""In-process async pub/sub. Pure stdlib + asyncio; no Redis dependency.

API-compatible subset of monorepo's `src.events.bus`:
  - Event(type, data, source, user_id, trace_id) with .to_dict()
  - EventBus.publish(event) — coroutine
  - EventBus.publish_fire_and_forget(event) — sync, schedules task
  - EventBus.publish_reliable(event, max_retries, base_delay) — coroutine
  - EventBus.subscribe(event_type, handler) — register coroutine handler
  - EventBus.channel(event_type) — returns minimal channel facade
  - module-level singleton `event_bus`
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

__all__ = ["Event", "EventBus", "event_bus"]

_log = logging.getLogger(__name__)

Handler = Callable[["Event"], Awaitable[None]]


class Event:
    __slots__ = ("data", "id", "source", "timestamp", "trace_id", "type", "user_id")

    def __init__(
        self,
        type: str,
        data: dict[str, Any],
        source: str = "",
        user_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.type = type
        self.data = data
        self.id = uuid.uuid4().hex
        self.timestamp = datetime.now(UTC)
        self.source = source
        self.user_id = user_id
        self.trace_id = trace_id or uuid.uuid4().hex

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
        }


class _Channel:
    """Minimal stand-in for monorepo's EventChannel — only .subscribe used."""

    def __init__(self, bus: EventBus, event_type: str) -> None:
        self._bus = bus
        self._event_type = event_type

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        return self._bus.subscribe(self._event_type, handler)


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> Callable[[], None]:
        self._handlers.setdefault(event_type, []).append(handler)

        def _unsubscribe() -> None:
            try:
                self._handlers.get(event_type, []).remove(handler)
            except ValueError:
                pass

        return _unsubscribe

    def channel(self, event_type: str) -> _Channel:
        return _Channel(self, event_type)

    async def publish(self, event: Event) -> None:
        for handler in list(self._handlers.get(event.type, [])):
            try:
                await handler(event)
            except Exception:
                _log.warning("handler failed for %s", event.type, exc_info=True)

    async def publish_reliable(
        self, event: Event, *, max_retries: int = 3, base_delay: float = 0.5
    ) -> bool:
        for attempt in range(1, max_retries + 1):
            try:
                await self.publish(event)
                return True
            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * attempt)
        return False

    def publish_fire_and_forget(self, event: Event) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.warning("no running loop; dropping event %s", event.type)
            return
        loop.create_task(self.publish(event))

    async def start(self) -> None:  # pragma: no cover - lifecycle stub
        return None

    async def stop(self) -> None:  # pragma: no cover - lifecycle stub
        self._handlers.clear()


event_bus = EventBus()
