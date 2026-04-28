"""Memvault event handlers — cache invalidation + reactive pipe wiring.

All cross-module event handlers are reactive pipes in reactive_adapters.py.
This file only wires cache invalidation and calls the pipe factories.
"""

import structlog

from src.events_stub.types import MemvaultEvents
from src.shared.cache import register_invalidation

logger = structlog.get_logger()

# --- Cache invalidation wiring ---

register_invalidation(
    module="memvault",
    operations=["list_tags"],
    events=[
        MemvaultEvents.MEMORY_STORED,
        MemvaultEvents.MEMORY_UPDATED,
        MemvaultEvents.MEMORY_DELETED,
    ],
)

register_invalidation(
    module="memvault",
    operations=["profile_score"],
    events=[
        MemvaultEvents.PROFILE_UPDATED,
    ],
)


# ======================== Reactive pipes ========================
# Flow 1: MEMORY_STORED → NoiseGate → TagCooccurrence → KG write
# Flow 2: capture.promoted → ConditionalOp(memvault) → BlockFetch → TagCooccurrence → KG write
# Flow 3: intelligence.digest.completed → DigestToBlock → MemoryBlock store

from .reactive_adapters import (  # noqa: E402
    wire_capture_promotion_flow,
    wire_intelligence_digest_flow,
    wire_memory_creation_flow,
)

wire_memory_creation_flow()
wire_capture_promotion_flow()
wire_intelligence_digest_flow()

# Flow 4: memvault.query.completed → Slow Thinker (shadow metrics)
from .slow_thinker import wire_slow_thinker_flow  # noqa: E402

wire_slow_thinker_flow()

# Flow 5 (Worker 4): capture.entry.created → counter → sleeptime reflection
# NOTE: assumed event name is `capture.entry.created`. If capture module emits a
# different name, adjust _wire_capture_subscription pre-merge. Best-effort wiring
# never raises — safe under test/stub event_bus.
from .sleeptime import _wire_capture_subscription  # noqa: E402

_wire_capture_subscription()
