"""events_stub — minimal in-process replacement for monorepo's `src.events`.

Re-exports the same public surface as `src.events.bus` and `src.events.types`
so memvault module code can import without modification:

    from src.events.bus import Event, event_bus
    from src.events.types import MemvaultEvents

In the OS build, callers should import from `src.events_stub.*` (or alias via
package layout) — this module supplies a pure-stdlib asyncio pub/sub backend.
"""

from .bus import Event, EventBus, event_bus
from .types import (
    AuthEvents,
    CaptureEvents,
    CompletionEvents,
    DocvaultEvents,
    IdeagraphEvents,
    MemvaultEvents,
    SearchIndexEvents,
    SessionIntelligenceEvents,
    SystemEvents,
    TaskflowEvents,
)

__all__ = [
    "AuthEvents",
    "CaptureEvents",
    "CompletionEvents",
    "DocvaultEvents",
    "Event",
    "EventBus",
    "IdeagraphEvents",
    "MemvaultEvents",
    "SearchIndexEvents",
    "SessionIntelligenceEvents",
    "SystemEvents",
    "TaskflowEvents",
    "event_bus",
]
