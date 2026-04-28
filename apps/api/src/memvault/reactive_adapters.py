"""Memvault Reactive Adapters — native reactive via EventBus.channel().

Six concepts:
  Subject      -> EventChannel (event_bus.channel())
  Observer     -> FunctionObserver (shared/reactive.py)
  Scheduler    -> EmbeddingScheduler (semaphore-gated concurrency)
  Observable   -> EventChannel.pipe() (_PipedChannel)
  Operator     -> NoiseGateOp + TagCooccurrenceOp + BlockFetchOp
  Subscription -> reactive.py Subscription (from channel.subscribe())
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from src.events_stub.bus import EventBus, event_bus
from src.events_stub.types import CaptureEvents, MemvaultEvents, SessionIntelligenceEvents
from src.shared.database import async_session_factory
from src.shared.reactive import (
    ConditionalOp,
    FunctionObserver,
    Pipeline,
    Subscription,
)
from text_ops.noise import check_noise

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# EmbeddingScheduler — Scheduler Protocol
# ═══════════════════════════════════════════════════════════════════════════


class EmbeddingScheduler:
    """Scheduler: semaphore-based concurrency control."""

    def __init__(self, max_concurrent: int = 5) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def schedule(self, work: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._semaphore:
            return await work(*args, **kwargs)

    async def schedule_batch(self, items: list, processor: Callable) -> list:
        async def _gated(item: Any) -> Any:
            async with self._semaphore:
                return await processor(item)

        return list(await asyncio.gather(*[_gated(item) for item in items]))


# ═══════════════════════════════════════════════════════════════════════════
# Creation Operators
# ═══════════════════════════════════════════════════════════════════════════


class NoiseGateOp:
    """Operator: content -> is_noise + noise_reason (wraps check_noise)."""

    @property
    def name(self) -> str:
        return "noise_gate"

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("content",)

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("is_noise", "noise_reason")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        content = ctx.get("content", "")
        verdict = check_noise(content)
        ctx["is_noise"] = verdict.is_noise
        ctx["noise_reason"] = verdict.reason
        return ctx


class TagCooccurrenceOp:
    """Operator: tags -> triple_dicts (tag co-occurrence extraction)."""

    @property
    def name(self) -> str:
        return "tag_cooccurrence"

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("tags",)

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("triple_dicts",)

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        tags = ctx.get("tags", [])
        tags = [t for t in tags if t != "_quarantine" and t.strip()]
        capped = tags[:5]

        triple_dicts = []
        for i, tag_a in enumerate(capped):
            for tag_b in capped[i + 1 :]:
                triple_dicts.append(
                    {
                        "subject": tag_a,
                        "predicate": "co_occurs_with",
                        "object": tag_b,
                    }
                )

        ctx["triple_dicts"] = triple_dicts
        return ctx


class BlockFetchOp:
    """Operator: promoted_id → tags, space_id, source_session, block_id (DB fetch)."""

    name = "block_fetch"
    input_keys = ("promoted_id",)
    output_keys = ("tags", "space_id", "source_session", "block_id")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        promoted_id = ctx.get("promoted_id")
        if not promoted_id:
            return ctx
        async with async_session_factory() as db:
            from .services import memory_block_service

            block = await memory_block_service.get(db, promoted_id)
            if block:
                ctx["tags"] = block.tags or []
                ctx["space_id"] = block.space_id
                ctx["source_session"] = block.source_session or f"block:{block.id}"
                ctx["block_id"] = str(block.id)
            # block 不存在 → tags 不注入 → TagCooccurrenceOp 產生空 triples → observer skip
        return ctx


class DigestToBlockOp:
    """Operator: digest event data → normalized block fields for storage."""

    name = "digest_to_block"
    input_keys = ("content",)
    output_keys = ("space_id", "block_type", "tags", "source_session")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        digest_type = ctx.get("digest_type", "weekly")
        period = ctx.get("period", "")
        extra_tags = ctx.get("tags", [])
        if isinstance(extra_tags, str):
            extra_tags = [extra_tags]

        ctx["space_id"] = ctx.get("space_id") or "default"
        ctx["block_type"] = "knowledge"
        ctx["tags"] = list(dict.fromkeys(["intelligence", "digest", digest_type, *extra_tags]))
        ctx["source_session"] = f"intelligence:{digest_type}:{period}"
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Shared KG write logic
# ═══════════════════════════════════════════════════════════════════════════


async def _write_triples_to_kg(
    triple_dicts: list[dict],
    space_id: str,
    source_session: str,
    log_prefix: str = "reactive",
) -> None:
    """共用 KG triple 批量寫入。被兩個 flow 的 observer 呼叫。"""
    try:
        async with async_session_factory() as db:
            from .kg_schemas import TripleBatchCreate, TripleCreate
            from .kg_services import triple_service

            batch = TripleBatchCreate(
                session_id=source_session,
                triples=[TripleCreate(**t) for t in triple_dicts],
            )
            result = await triple_service.batch_ingest(db, space_id, batch)
            await db.commit()
            logger.info(
                f"{log_prefix}.block_to_kg",
                extra={
                    "source_session": source_session,
                    "triples_created": result.get("ingested", 0),
                },
            )
    except Exception:
        logger.warning(
            f"{log_prefix}.block_to_kg_failed",
            extra={"source_session": source_session},
            exc_info=True,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Flow 1: MEMORY_STORED → KG (existing)
# ═══════════════════════════════════════════════════════════════════════════


def wire_memory_creation_flow(
    bus: EventBus | None = None,
) -> Subscription:
    """Memory Creation Flow: channel -> pipe(operators) -> observer -> KG write.

    1. Subject      - event_bus.channel(MEMORY_STORED)
    2. Observable   - channel.pipe(NoiseGateOp, TagCooccurrenceOp)
    3. Operator     - NoiseGateOp + TagCooccurrenceOp
    4. Observer     - FunctionObserver(_kg_ingest_handler) — writes KG triples
    5. Scheduler    - EmbeddingScheduler (available for future observer use)
    6. Subscription - piped.subscribe(observer), auto-cleaned by EventBus.stop()
    """
    _bus = bus or event_bus

    # Operators
    noise_gate = NoiseGateOp()
    tag_cooccurrence = TagCooccurrenceOp()

    # Channel + pipe
    piped = _bus.channel(MemvaultEvents.MEMORY_STORED).pipe(noise_gate, tag_cooccurrence)

    # compile() pre-flight
    check_pipe = Pipeline(name="memory_creation").pipe(noise_gate, tag_cooccurrence)
    initial = {"content", "tags", "block_id", "space_id", "source_session"}
    missing = check_pipe.compile(initial_keys=initial)
    if missing:
        logger.error("memory creation flow compile failed: %s", missing)

    # Observer — writes KG triples
    async def _kg_ingest_handler(ctx: dict[str, Any]) -> None:
        if ctx.get("is_noise"):
            logger.debug("reactive.noise_gated", extra={"reason": ctx.get("noise_reason")})
            return

        triple_dicts = ctx.get("triple_dicts", [])
        if not triple_dicts:
            return

        space_id = ctx.get("space_id")
        if not space_id:
            return

        source_session = ctx.get("source_session") or f"block:{ctx.get('block_id', 'unknown')}"
        await _write_triples_to_kg(triple_dicts, space_id, source_session, log_prefix="reactive")

    observer = FunctionObserver(_kg_ingest_handler, name="kg_triple_ingest")

    # Subscription
    return piped.subscribe(observer)


# ═══════════════════════════════════════════════════════════════════════════
# Flow 2: capture.promoted → memvault KG enrichment (cross-module pipe)
# ═══════════════════════════════════════════════════════════════════════════


def wire_capture_promotion_flow(
    bus: EventBus | None = None,
) -> Subscription:
    """Cross-module pipe: capture.promoted → memvault KG enrichment.

    ConditionalOp(module==memvault) → Pipeline(BlockFetch → TagCooccurrence) → Observer(KG write)
    """
    _bus = bus or event_bus

    memvault_pipe = Pipeline(name="memvault_kg").pipe(BlockFetchOp(), TagCooccurrenceOp())

    piped = _bus.channel(CaptureEvents.PROMOTED).pipe(
        ConditionalOp(
            predicate=lambda ctx: ctx.get("module") == "memvault" and bool(ctx.get("promoted_id")),
            then_op=memvault_pipe,
            name="memvault_promotion_gate",
            predicate_keys=("module", "promoted_id"),
        ),
    )

    # compile() pre-flight: validate key chain
    outer = Pipeline().pipe(
        ConditionalOp(
            predicate=lambda ctx: True,
            then_op=memvault_pipe,
            name="_compile_check",
            predicate_keys=("module", "promoted_id"),
        ),
    )
    missing = outer.compile(initial_keys={"module", "promoted_id", "capture_id", "entity_type"})
    if missing:
        logger.error("capture promotion flow compile failed: %s", missing)

    async def _capture_kg_write_handler(ctx: dict[str, Any]) -> None:
        """Observer: write KG triples from capture promotion."""
        triple_dicts = ctx.get("triple_dicts", [])
        if not triple_dicts:
            return

        space_id = ctx.get("space_id")
        if not space_id:
            return

        source_session = ctx.get("source_session") or f"block:{ctx.get('block_id', 'unknown')}"
        await _write_triples_to_kg(
            triple_dicts, space_id, source_session, log_prefix="flywheel.capture_promoted"
        )

    observer = FunctionObserver(_capture_kg_write_handler, name="capture_to_kg")
    return piped.subscribe(observer)


# ═══════════════════════════════════════════════════════════════════════════
# Flow 3: intelligence.digest → memvault block (cross-module pipe)
# ═══════════════════════════════════════════════════════════════════════════


def wire_intelligence_digest_flow(
    bus: EventBus | None = None,
) -> Subscription:
    """Cross-module pipe: intelligence.digest.completed → memvault block storage.

    DigestToBlockOp → Observer(create MemoryBlock)
    """
    _bus = bus or event_bus

    digest_op = DigestToBlockOp()
    piped = _bus.channel(SessionIntelligenceEvents.DIGEST_COMPLETED).pipe(digest_op)

    # compile() pre-flight
    check = Pipeline(name="intelligence_digest").pipe(digest_op)
    missing = check.compile(initial_keys={"content", "space_id", "digest_type", "period", "tags"})
    if missing:
        logger.error("intelligence digest flow compile failed: %s", missing)

    async def _digest_store_handler(ctx: dict[str, Any]) -> None:
        """Observer: create MemoryBlock from intelligence digest."""
        content = ctx.get("content", "")
        if not content:
            return

        space_id = ctx.get("space_id", "default")

        try:
            async with async_session_factory() as db:
                from .schemas import MemoryBlockCreate
                from .services import memory_block_service

                block_data = MemoryBlockCreate(
                    content=content,
                    block_type=ctx.get("block_type", "knowledge"),
                    tags=ctx.get("tags", []),
                    source_session=ctx.get("source_session", ""),
                )
                await memory_block_service.create(db, space_id, block_data)
                await db.commit()
                logger.info(
                    "flywheel.intelligence_to_memvault",
                    extra={
                        "digest_type": ctx.get("digest_type"),
                        "period": ctx.get("period"),
                        "space_id": space_id,
                    },
                )
        except Exception:
            logger.warning(
                "flywheel.intelligence_to_memvault_failed",
                exc_info=True,
            )

    observer = FunctionObserver(_digest_store_handler, name="digest_to_block")
    return piped.subscribe(observer)
