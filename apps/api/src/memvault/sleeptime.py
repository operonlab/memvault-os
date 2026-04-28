"""Memvault Sleeptime Reflection Agent (Worker 4 — Phase 1).

Reactive background reflection — every N capture events triggers a lightweight
health-check + hot-snapshot update. Inspired by Letta's sleeptime model.

Flow:
    capture.entry.created  →  maybe_trigger_sleeptime(space_id, count)
                              ├─ if count % SLEEPTIME_INTERVAL != 0 → noop
                              └─ else asyncio.ensure_future(_run_sleeptime(...))
                                       ├─ lint health-check (best-effort)
                                       ├─ update_block(space_id, "project", ...)
                                       │  + ensure persona / human placeholder rows
                                       └─ emit memvault.sleeptime.completed event

Module boundaries:
    - reads capture event payload (space_id only)
    - writes only memvault schema (memory_block table)
    - never imports another module's models.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.events_stub.types import CaptureEvents
from src.shared.database import async_session_factory

from .models import MemoryBlock, MemoryBlockSnapshot

logger = logging.getLogger(__name__)

# Trigger interval — every Nth capture event triggers sleeptime
# (Settings layer not yet wired into memvault; constant here is the source of truth
#  until pydantic-settings field is added in Phase 2.)
SLEEPTIME_INTERVAL: int = 5

BLOCK_TYPES: tuple[str, ...] = ("persona", "human", "project")
PROJECT_SUMMARY_RECENT_N: int = 5
PROJECT_SUMMARY_PER_BLOCK_CHARS: int = 30

_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def maybe_trigger_sleeptime(space_id: str, capture_count: int) -> bool:
    """Trigger sleeptime reflection iff capture_count aligns with interval.

    Returns True iff a background reflection task was scheduled.
    Fire-and-forget — caller does not await the reflection itself.
    """
    if not space_id:
        return False
    if capture_count <= 0:
        return False
    if capture_count % SLEEPTIME_INTERVAL != 0:
        return False

    task = asyncio.ensure_future(_run_sleeptime(space_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return True


# ---------------------------------------------------------------------------
# Reflection runner
# ---------------------------------------------------------------------------


async def _run_sleeptime(space_id: str) -> dict:
    """Run a single sleeptime reflection pass for one space.

    Steps:
      1. lint health-check (best-effort — fall back to legacy contradiction check
         until Worker 3 lands run_health_check).
      2. Update `project` hot-snapshot block from recent memory.
      3. Ensure `persona` / `human` placeholder rows exist (Worker 5 fills content).
      4. Emit memvault.sleeptime.completed event (best-effort).

    Resilient — never raises; logs and degrades.
    """
    findings: list = []
    blocks_updated: list[str] = []

    try:
        # 1. Health-check — best-effort. Worker 3 will land run_health_check; until
        #    then fall back to existing contradiction check. Both are optional.
        findings = await _safe_health_check(space_id)

        # 2 + 3. Update multi-block hot snapshot (project only; persona/human placeholder)
        async with async_session_factory() as db:
            project_summary = await _summarize_recent(db, space_id)

            await _ensure_block(db, space_id, "project", project_summary)
            blocks_updated.append("project")

            # Worker 5 territory: persona / human stay empty (content=None)
            await _ensure_block(db, space_id, "persona", None)
            await _ensure_block(db, space_id, "human", None)

            await db.commit()

        # 4. Emit event (best-effort)
        await _emit_sleeptime_completed(
            space_id=space_id,
            findings_count=len(findings),
            blocks_updated=blocks_updated,
        )

        logger.info(
            "memvault.sleeptime: space_id=%s findings=%d blocks_updated=%s",
            space_id,
            len(findings),
            blocks_updated,
        )
    except Exception:
        logger.warning("memvault.sleeptime failed: space_id=%s", space_id, exc_info=True)

    return {
        "space_id": space_id,
        "findings_count": len(findings),
        "blocks_updated": blocks_updated,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _safe_health_check(space_id: str) -> list:
    """Best-effort health-check.

    Worker 3 will provide `lint.run_health_check(space_id) -> list[Finding]`. Until
    then, fall back to existing `lint.check_contradictions(space_id)` if available.
    Any failure → return [] and continue.
    """
    try:
        from . import lint  # local import — avoid circular at module-load time
    except Exception:
        logger.debug("memvault.sleeptime: lint module unavailable")
        return []

    fn = getattr(lint, "run_health_check", None)
    if fn is None:
        fn = getattr(lint, "check_contradictions", None)
        # TODO(worker-3): switch to lint.run_health_check once it lands.
    if fn is None:
        return []

    try:
        result = fn(space_id)
        if asyncio.iscoroutine(result):
            result = await result
        return list(result or [])
    except Exception:
        logger.warning(
            "memvault.sleeptime: health-check failed for space_id=%s",
            space_id,
            exc_info=True,
        )
        return []


async def _summarize_recent(db, space_id: str) -> str:
    """Placeholder summary: concat first N chars of N most-recent blocks.

    Worker 5 will replace this with a proper LLM-driven summary.
    """
    stmt = (
        select(MemoryBlock)
        .where(MemoryBlock.space_id == space_id)
        .where(MemoryBlock.deleted_at.is_(None))
        .order_by(MemoryBlock.created_at.desc())
        .limit(PROJECT_SUMMARY_RECENT_N)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    if not rows:
        return ""

    # Defensive Python-side cap — SQL `.limit(N)` already constrains this,
    # but we keep an explicit slice so the contract holds even if the upstream
    # query is mutated to drop the limit (and to make the unit test deterministic
    # against fake sessions that don't honor SQL LIMIT).
    rows = rows[:PROJECT_SUMMARY_RECENT_N]

    parts: list[str] = []
    for row in rows:
        text_ = (row.content or "").strip().replace("\n", " ")
        if not text_:
            continue
        parts.append(text_[:PROJECT_SUMMARY_PER_BLOCK_CHARS])
    return " | ".join(parts)


async def _ensure_block(
    db,
    space_id: str,
    block_type: str,
    content: str | None,
) -> MemoryBlockSnapshot:
    """Upsert a (space_id, block_type) snapshot row. Bumps version on content change."""
    stmt = (
        select(MemoryBlockSnapshot)
        .where(MemoryBlockSnapshot.space_id == space_id)
        .where(MemoryBlockSnapshot.block_type == block_type)
        .where(MemoryBlockSnapshot.deleted_at.is_(None))
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    word_count = len((content or "").split()) if content else 0

    if existing is None:
        # `id` defaults to uuid7().hex via TimestampMixin
        block = MemoryBlockSnapshot(
            space_id=space_id,
            block_type=block_type,
            content=content,
            word_count=word_count,
            block_version=1,
        )
        db.add(block)
        return block

    if (existing.content or "") != (content or ""):
        existing.content = content
        existing.word_count = word_count
        existing.block_version = (existing.block_version or 1) + 1
        existing.updated_at = datetime.now(UTC)
    return existing


async def _emit_sleeptime_completed(
    *,
    space_id: str,
    findings_count: int,
    blocks_updated: list[str],
) -> None:
    """Publish memvault.sleeptime.completed (best-effort, never raises)."""
    try:
        from src.events_stub.bus import event_bus

        payload = {
            "space_id": space_id,
            "findings_count": findings_count,
            "blocks_updated": list(blocks_updated),
        }
        publish = getattr(event_bus, "publish", None)
        if publish is None:
            return

        result = publish("memvault.sleeptime.completed", payload)
        if asyncio.iscoroutine(result):
            # Fire-and-forget — never block sleeptime on event delivery.
            # Keep a reference so the task is not GC'd mid-flight.
            task = asyncio.ensure_future(result)
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
    except Exception:
        logger.debug("memvault.sleeptime: emit completed event failed", exc_info=True)


# ---------------------------------------------------------------------------
# Capture event subscription
# ---------------------------------------------------------------------------


async def _on_capture_entry_created(event) -> None:
    """Subscriber wired to `capture.created` (CaptureEvents.CREATED).

    Increments a per-space Redis counter and triggers sleeptime when aligned.
    Falls back to in-process counter if Redis is unavailable.

    Payload shape (from capture.services._create): includes `space_id`,
    `capture_id`, `module`, `entity_type`, `raw_input`, `completeness`.
    """
    data = getattr(event, "data", None) or {}
    space_id = data.get("space_id") or getattr(event, "space_id", None)
    if not space_id:
        return

    count = await _incr_capture_count(space_id)
    await maybe_trigger_sleeptime(space_id, count)


# In-process fallback counter (per-process; resets on restart)
_inproc_counts: dict[str, int] = {}


async def _incr_capture_count(space_id: str) -> int:
    """Increment per-space capture counter. Redis primary, in-proc fallback."""
    key = f"memvault:capture_count:{space_id}"
    try:
        from src.shared.cache import get_redis  # type: ignore

        redis_client = get_redis()
        if redis_client is not None:
            value = await redis_client.incr(key)
            return int(value)
    except Exception:
        logger.debug("memvault.sleeptime: redis counter unavailable", exc_info=True)

    _inproc_counts[space_id] = _inproc_counts.get(space_id, 0) + 1
    return _inproc_counts[space_id]


def _wire_capture_subscription() -> None:
    """Subscribe to CaptureEvents.CREATED ("capture.created") if event_bus is available.

    Idempotent — safe to call multiple times during module import (events.py).
    Best-effort — never raises (test envs may stub event_bus).
    """
    try:
        from src.events_stub.bus import event_bus

        channel_fn = getattr(event_bus, "channel", None)
        if channel_fn is not None:
            ch = channel_fn(CaptureEvents.CREATED)
            sub = getattr(ch, "subscribe_handler", None) or getattr(ch, "subscribe", None)
            if sub is not None:
                sub(_on_capture_entry_created)
                return

        # Fallback shapes
        sub_fn = getattr(event_bus, "subscribe", None)
        if sub_fn is not None:
            sub_fn(CaptureEvents.CREATED, _on_capture_entry_created)
    except Exception:
        logger.debug("memvault.sleeptime: capture subscription wiring skipped", exc_info=True)


__all__ = [
    "BLOCK_TYPES",
    "SLEEPTIME_INTERVAL",
    "_ensure_block",
    "_on_capture_entry_created",
    "_run_sleeptime",
    "_summarize_recent",
    "_wire_capture_subscription",
    "maybe_trigger_sleeptime",
]
