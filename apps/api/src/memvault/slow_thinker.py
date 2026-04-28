"""Memvault Slow Thinker — predictive prefetch pipeline.

Phase A: Shadow write — records QUERY_COMPLETED events to metrics.
Phase B1: Background prefetch (not serving) — predicts + prefetches + caches.
Phase B2: Serve speculative hits.
Phase C: Eviction + dashboard.

Inspired by VoiceAgentRAG (Salesforce AI Research).
"""

from __future__ import annotations

import logging
import time as _time
from collections import Counter
from typing import Any

from sqlalchemy import select

from src.events_stub.bus import EventBus, event_bus
from src.events_stub.types import MemvaultEvents
from src.shared.database import async_session_factory
from src.shared.prefetch import PrefetchFingerprint, SpeculativePrefetchCache
from src.shared.reactive import FunctionObserver, Pipeline, Subscription

logger = logging.getLogger(__name__)

_prefetch_cache = SpeculativePrefetchCache(module="memvault", default_ttl=300)

# Minimum queries before hit_rate check kicks in (prevents self-disabling on cold start)
_MIN_SAMPLE_THRESHOLD = 50


# ═══════════════════════════════════════════════════════════════════════════
# Phase A: Shadow Write
# ═══════════════════════════════════════════════════════════════════════════


class QueryEventRecorderOp:
    """Records QUERY_COMPLETED events to prefetch metrics (shadow write only).

    Does NOT write to cache or modify query results.
    """

    name = "query_event_recorder"
    input_keys = (
        "space_id",
        "query",
        "intent",
        "tags",
        "consumer",
        "task_mode",
        "thinking_mode_used",
        "load_budget",
        "result_count",
    )
    output_keys = ("recorded",)

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        space_id = ctx.get("space_id")
        if space_id:
            await _prefetch_cache.record_query(space_id)
        ctx["recorded"] = True
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Phase B1: Admission Gate
# ═══════════════════════════════════════════════════════════════════════════


class AdmissionGateOp:
    """Decides whether this query event should trigger a background prefetch.

    Skip rules (ordered by cost):
    1. consumer == "ui" → skip (human browsing, unpredictable)
    2. thinking_mode_used == "slow" → skip (already got deep results)
    3. result_count == 0 → skip (no context to predict from)
    4. In-flight lock: same fingerprint within 5s → skip
    5. hit_rate < 0.05 AND prefetch_count > MIN_SAMPLE → skip
    """

    name = "admission_gate"
    input_keys = (
        "space_id",
        "consumer",
        "thinking_mode_used",
        "result_count",
        "intent",
        "tags",
        "task_mode",
        "load_budget",
    )
    output_keys = ("should_prefetch", "skip_reason")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        space_id = ctx.get("space_id", "")
        consumer = ctx.get("consumer", "")
        thinking_mode = ctx.get("thinking_mode_used", "")
        result_count = ctx.get("result_count", 0)

        # Rule 1: UI consumer → skip
        if consumer == "ui":
            ctx["should_prefetch"] = False
            ctx["skip_reason"] = "consumer_ui"
            await _prefetch_cache.record_skip(space_id)
            return ctx

        # Rule 2: Already walked slow path
        if thinking_mode == "slow":
            ctx["should_prefetch"] = False
            ctx["skip_reason"] = "already_slow"
            await _prefetch_cache.record_skip(space_id)
            return ctx

        # Rule 3: No results → unstable context
        if result_count == 0:
            ctx["should_prefetch"] = False
            ctx["skip_reason"] = "no_results"
            await _prefetch_cache.record_skip(space_id)
            return ctx

        # Rule 4: Low hit rate (only after minimum samples) — check BEFORE lock
        metrics = await _prefetch_cache.get_metrics(space_id)
        if metrics.prefetch_count > _MIN_SAMPLE_THRESHOLD and metrics.hit_rate < 0.05:
            ctx["should_prefetch"] = False
            ctx["skip_reason"] = "low_hit_rate"
            await _prefetch_cache.record_skip(space_id)
            return ctx

        # Rule 5: In-flight dedup lock — AFTER cheap checks, to avoid wasting locks
        fp = _build_fingerprint_from_ctx(ctx)
        if not await _prefetch_cache.try_acquire_inflight(fp):
            ctx["should_prefetch"] = False
            ctx["skip_reason"] = "inflight_locked"
            await _prefetch_cache.record_skip(space_id)
            return ctx

        ctx["should_prefetch"] = True
        ctx["skip_reason"] = None
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Phase B1: Intent Predictor
# ═══════════════════════════════════════════════════════════════════════════


class IntentPredictorOp:
    """Rule-based prediction of next query intent. Reads QueryJournal DB.

    Rules (no LLM):
    1. Last 3 queries same intent → predict same (momentum)
    2. entity_lookup → predict factual (drill-down)
    3. conceptual → predict exploratory (broadening)
    4. Tag momentum: top 3 from last 5 queries
    5. Cold start: fallback to most common intent in last 7 days
    """

    name = "intent_predictor"
    input_keys = ("space_id", "should_prefetch", "consumer", "task_mode", "intent", "tags")
    output_keys = ("predicted_fingerprint",)

    # Intent transition rules: current_intent → predicted_next
    _TRANSITIONS = {
        "entity_lookup": "factual",
        "conceptual": "exploratory",
        "exploratory": "conceptual",
        "cross_domain": "factual",
    }

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if not ctx.get("should_prefetch"):
            ctx["predicted_fingerprint"] = None
            return ctx

        space_id = ctx["space_id"]
        current_intent = ctx.get("intent", "unknown")
        current_tags = ctx.get("tags", [])

        # Fetch recent journals from DB
        recent = await self._get_recent_journals(space_id, limit=5)

        # Rule 1: Momentum — if last 3 queries had same intent, predict same
        if len(recent) >= 3:
            recent_intents = [r.routing_intent for r in recent[:3] if r.routing_intent]
            if len(recent_intents) == 3 and len(set(recent_intents)) == 1:
                predicted_intent = recent_intents[0]
                predicted_tags = self._extract_tag_momentum(recent, current_tags)
                ctx["predicted_fingerprint"] = self._build_fp(
                    space_id, ctx, predicted_intent, predicted_tags
                )
                return ctx

        # Rule 2-3: Transition rules
        predicted_intent = self._TRANSITIONS.get(current_intent, current_intent)

        # Rule 4: Tag momentum
        predicted_tags = self._extract_tag_momentum(recent, current_tags)

        ctx["predicted_fingerprint"] = self._build_fp(
            space_id, ctx, predicted_intent, predicted_tags
        )
        return ctx

    def _extract_tag_momentum(self, recent: list, current_tags: list[str]) -> list[str]:
        """Extract top 3 most frequent tags from recent queries + current."""
        all_tags: list[str] = list(current_tags)
        for r in recent:
            if r.top_entity_ids:
                all_tags.extend(r.top_entity_ids[:3])
        if not all_tags:
            return []
        counter = Counter(all_tags)
        return [tag for tag, _ in counter.most_common(3)]

    def _build_fp(
        self, space_id: str, ctx: dict, intent: str, tags: list[str]
    ) -> PrefetchFingerprint:
        return PrefetchFingerprint(
            module="memvault",
            space_id=space_id,
            fields={
                "consumer": ctx.get("consumer", "human"),
                "task_mode": ctx.get("task_mode", "build"),
                "intent": intent,
            },
        )

    async def _get_recent_journals(self, space_id: str, limit: int = 5) -> list:
        """Read recent QueryJournal entries from DB."""
        try:
            from .models import QueryJournal

            async with async_session_factory() as db:
                q = (
                    select(QueryJournal)
                    .where(
                        QueryJournal.space_id == space_id,
                        QueryJournal.deleted_at.is_(None),
                    )
                    .order_by(QueryJournal.created_at.desc())
                    .limit(limit)
                )
                result = await db.execute(q)
                return list(result.scalars().all())
        except Exception:
            logger.debug("slow_thinker.get_recent_journals failed", exc_info=True)
            return []


# ═══════════════════════════════════════════════════════════════════════════
# Phase B1: Prefetch Executor
# ═══════════════════════════════════════════════════════════════════════════


class PrefetchExecutorOp:
    """Executes search with predicted parameters. Results are sanitized."""

    name = "prefetch_executor"
    input_keys = ("space_id", "predicted_fingerprint")
    output_keys = ("prefetch_cards", "execution_ms")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        fp = ctx.get("predicted_fingerprint")
        if not fp:
            ctx["prefetch_cards"] = []
            ctx["execution_ms"] = 0.0
            return ctx

        t0 = _time.monotonic()
        try:
            cards = await self._run_search(fp)
            ctx["prefetch_cards"] = cards
        except Exception:
            logger.debug("slow_thinker.prefetch_executor failed", exc_info=True)
            ctx["prefetch_cards"] = []
        ctx["execution_ms"] = round((_time.monotonic() - t0) * 1000, 1)
        return ctx

    async def _run_search(self, fp: PrefetchFingerprint) -> list[dict]:
        """Execute search using predicted tags as synthetic query."""
        from .injection_guard import sanitize_for_injection
        from .query_runtime import _search_blocks

        tags = fp.fields.get("tags", "").split(",")
        tags = [t.strip() for t in tags if t.strip()]
        if not tags:
            return []

        synthetic_query = " ".join(tags)
        task_mode = fp.fields.get("task_mode", "build")
        top_k = int(fp.fields.get("top_k", "6"))

        async with async_session_factory() as db:
            # Reuse existing search infrastructure
            search_results, _ = await _search_blocks(db, fp.space_id, synthetic_query, top_k=top_k)

            # Dispatch by block_type: attitude blocks flow through same search (KAS: Block = SSoT)
            cards = []
            for result in search_results[:top_k]:
                block = result.block
                safe_content = sanitize_for_injection(block.content or "")
                if block.block_type == "attitude":
                    category = (block.tags or ["preference"])[0]
                    cards.append(
                        {
                            "id": f"prefetch:attitude:{block.id}",
                            "title": f"偏好 / {category}",
                            "summary": safe_content[:180],
                            "why_relevant": "預測式預載 — 相關工作偏好。",
                            "use_now": "延續這個偏好或工作原則。",
                            "layer": "fast",
                            "source_type": "attitude",
                            "confidence": round(float(result.score or 0.5), 3),
                            "tags": [category],
                            "evidence_refs": [],
                            "source": "speculative_prefetch",
                        }
                    )
                else:
                    cards.append(
                        {
                            "id": f"prefetch:block:{block.id}",
                            "title": f"{block.block_type} / {(block.tags or [''])[0]}",
                            "summary": safe_content[:180],
                            "why_relevant": "預測式預載 — 基於近期查詢模式。",
                            "use_now": f"預載上下文：{safe_content[:70]}",
                            "layer": "fast",
                            "source_type": "block",
                            "confidence": round(float(result.score or 0.5), 3),
                            "tags": list(block.tags or []),
                            "evidence_refs": [],
                            "source": "speculative_prefetch",
                        }
                    )

            return cards


# ═══════════════════════════════════════════════════════════════════════════
# Phase B1: Cache Writer
# ═══════════════════════════════════════════════════════════════════════════


class CacheWriterOp:
    """Writes prefetch results to speculative cache + records metrics."""

    name = "cache_writer"
    input_keys = ("predicted_fingerprint", "prefetch_cards", "execution_ms")
    output_keys = ("cache_key_written", "metrics_recorded")

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        fp = ctx.get("predicted_fingerprint")
        cards = ctx.get("prefetch_cards", [])
        execution_ms = ctx.get("execution_ms", 0.0)

        if not fp or not cards:
            ctx["cache_key_written"] = None
            ctx["metrics_recorded"] = False
            return ctx

        await _prefetch_cache.set(fp, cards)
        await _prefetch_cache.record_prefetch(fp.space_id, execution_ms)
        ctx["cache_key_written"] = fp.cache_key
        ctx["metrics_recorded"] = True
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Phase C: Eviction
# ═══════════════════════════════════════════════════════════════════════════


class EvictionOp:
    """Evicts low-value prefetch entries from speculative cache.

    Called periodically (e.g., via Cronicle every 10 minutes), not inline in the pipeline.
    Uses scan to find prefetch:memvault:* keys and removes those past TTL.
    Redis TTL handles most eviction; this is for proactive cleanup and waste tracking.
    """

    name = "eviction"
    input_keys = ("space_id",)
    output_keys = ("evicted_count",)

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        space_id = ctx.get("space_id", "default")
        evicted = 0
        try:
            from src.shared.redis import get_redis

            r = get_redis()
            pattern = f"prefetch:memvault:{space_id}:*"
            async for key in r.scan_iter(match=pattern, count=50):
                ttl = await r.ttl(key)
                # Evict entries with TTL < 30s (nearly expired, likely unused)
                if 0 < ttl < 30:
                    await r.delete(key)
                    await _prefetch_cache.record_waste(space_id)
                    evicted += 1
        except Exception:
            logger.debug("slow_thinker.eviction failed", exc_info=True)

        ctx["evicted_count"] = evicted
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _build_fingerprint_from_ctx(ctx: dict[str, Any]) -> PrefetchFingerprint:
    """Build a PrefetchFingerprint from event context for in-flight lock."""
    return PrefetchFingerprint(
        module="memvault",
        space_id=ctx.get("space_id", ""),
        fields={
            "consumer": ctx.get("consumer", "human"),
            "task_mode": ctx.get("task_mode", "build"),
            "intent": ctx.get("intent", "unknown"),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Flow wiring
# ═══════════════════════════════════════════════════════════════════════════


def wire_slow_thinker_flow(
    bus: EventBus | None = None,
) -> Subscription:
    """Wire the Slow Thinker pipeline to QUERY_COMPLETED events.

    Phase B1: Full background prefetch pipeline (not serving to hot path yet).
    QUERY_COMPLETED → Recorder → AdmissionGate → IntentPredictor → PrefetchExecutor → CacheWriter
    """
    _bus = bus or event_bus

    ops = [
        QueryEventRecorderOp(),
        AdmissionGateOp(),
        IntentPredictorOp(),
        PrefetchExecutorOp(),
        CacheWriterOp(),
    ]

    # compile() pre-flight check
    check = Pipeline(name="slow_thinker_b1").pipe(*ops)
    initial_keys = {
        "space_id",
        "query",
        "intent",
        "tags",
        "consumer",
        "task_mode",
        "thinking_mode_used",
        "load_budget",
        "result_count",
    }
    missing = check.compile(initial_keys=initial_keys)
    if missing:
        logger.error("slow_thinker compile failed: missing keys %s", missing)

    # Channel + pipe
    piped = _bus.channel(MemvaultEvents.QUERY_COMPLETED).pipe(*ops)

    # Observer — background fire-and-forget handler
    async def _slow_thinker_handler(ctx: dict[str, Any]) -> None:
        cache_key = ctx.get("cache_key_written")
        if cache_key:
            logger.info(
                "slow_thinker.prefetched",
                extra={
                    "space_id": ctx.get("space_id"),
                    "cache_key": cache_key,
                    "execution_ms": ctx.get("execution_ms", 0),
                    "card_count": len(ctx.get("prefetch_cards", [])),
                },
            )

    observer = FunctionObserver(_slow_thinker_handler, name="slow_thinker_b1")
    return piped.subscribe(observer)
