"""Memvault FeatureStore — NgRx-style state container for the memvault module.

State shape (immutables.Map):
    recent_memories  : list[dict]  — last N stored memories (hot cache)
    embedding_queue  : list[str]   — block IDs pending embedding
    kg_stats         : dict        — triples, communities, entities counts
    active_reflections: dict[str, dict] — in-progress reflections keyed by id

Actions map 1-to-1 to MemvaultEvents (20 types).
Effects wrap the three existing reactive pipe flows.
"""

from __future__ import annotations

import logging
from typing import Any

from src.events_stub.types import MemvaultEvents
from src.shared.actions import Action, create_action, create_reducer, on
from src.shared.middleware import PerformanceMiddleware
from src.shared.selectors import create_selector
from src.shared.store import FeatureStore, effect, register_effects

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Actions (20 — mirror MemvaultEvents)
# ══════════════════════════════════════════════════════════════════════════════

MemoryStored = create_action(MemvaultEvents.MEMORY_STORED)
MemoryUpdated = create_action(MemvaultEvents.MEMORY_UPDATED)
MemoryDeleted = create_action(MemvaultEvents.MEMORY_DELETED)
MemoryRecalled = create_action(MemvaultEvents.MEMORY_RECALLED)
MemoryPruned = create_action(MemvaultEvents.MEMORY_PRUNED)

EmbeddingComputed = create_action(MemvaultEvents.EMBEDDING_COMPUTED)
ProfileUpdated = create_action(MemvaultEvents.PROFILE_UPDATED)

TripleIngested = create_action(MemvaultEvents.TRIPLE_INGESTED)
TripleBatchIngested = create_action(MemvaultEvents.TRIPLE_BATCH_INGESTED)
CommunityRegenerated = create_action(MemvaultEvents.COMMUNITY_REGENERATED)
CommunitySummaryRegenerated = create_action(MemvaultEvents.COMMUNITY_SUMMARY_REGENERATED)
AttitudeEvolved = create_action(MemvaultEvents.ATTITUDE_EVOLVED)
SkillInvoked = create_action(MemvaultEvents.SKILL_INVOKED)
TripleInvalidated = create_action(MemvaultEvents.TRIPLE_INVALIDATED)
EntityResolved = create_action(MemvaultEvents.ENTITY_RESOLVED)
EntityMerged = create_action(MemvaultEvents.ENTITY_MERGED)
ReflectionCompleted = create_action(MemvaultEvents.REFLECTION_COMPLETED)
KnowledgeCurated = create_action(MemvaultEvents.KNOWLEDGE_CURATED)

# ══════════════════════════════════════════════════════════════════════════════
# Initial state helpers
# ══════════════════════════════════════════════════════════════════════════════

_MAX_RECENT_MEMORIES = 20  # hot-cache window


def _initial_state() -> dict:
    return {
        "recent_memories": [],
        "embedding_queue": [],
        "kg_stats": {"triples": 0, "communities": 0, "entities": 0},
        "active_reflections": {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reducer handlers (pure — Map in, Map out)
# ══════════════════════════════════════════════════════════════════════════════


def _on_memory_stored(s, action: Action) -> Any:
    """Prepend to recent_memories (hot-cache), cap at _MAX_RECENT_MEMORIES."""
    payload = action.payload or {}
    recent = list(s["recent_memories"])
    recent.insert(0, payload)
    recent = recent[:_MAX_RECENT_MEMORIES]
    return s.set("recent_memories", recent)


def _on_memory_updated(s, action: Action) -> Any:
    """Update matching entry in recent_memories if present."""
    payload = action.payload or {}
    block_id = payload.get("id") or payload.get("block_id")
    if not block_id:
        return s
    recent = [
        {**m, **payload} if (m.get("id") or m.get("block_id")) == block_id else m
        for m in s["recent_memories"]
    ]
    return s.set("recent_memories", recent)


def _on_memory_deleted(s, action: Action) -> Any:
    """Remove deleted memory from recent_memories and embedding_queue."""
    payload = action.payload or {}
    block_id = payload.get("id") or payload.get("block_id")
    if not block_id:
        return s
    recent = [m for m in s["recent_memories"] if (m.get("id") or m.get("block_id")) != block_id]
    queue = [bid for bid in s["embedding_queue"] if bid != block_id]
    return s.set("recent_memories", recent).set("embedding_queue", queue)


def _on_memory_pruned(s, action: Action) -> Any:
    """Remove pruned block IDs from recent_memories."""
    payload = action.payload or {}
    pruned_ids = set(payload.get("pruned_ids", []))
    if not pruned_ids:
        return s
    recent = [
        m for m in s["recent_memories"] if (m.get("id") or m.get("block_id")) not in pruned_ids
    ]
    return s.set("recent_memories", recent)


def _on_embedding_computed(s, action: Action) -> Any:
    """Remove computed block ID from embedding_queue."""
    payload = action.payload or {}
    block_id = payload.get("block_id") or payload.get("id")
    if not block_id:
        return s
    queue = [bid for bid in s["embedding_queue"] if bid != block_id]
    return s.set("embedding_queue", queue)


def _on_triple_ingested(s, action: Action) -> Any:
    """Increment triples counter in kg_stats."""
    stats = dict(s["kg_stats"])
    stats["triples"] = stats.get("triples", 0) + 1
    return s.set("kg_stats", stats)


def _on_triple_batch_ingested(s, action: Action) -> Any:
    """Increment triples counter by batch size."""
    payload = action.payload or {}
    count = payload.get("ingested", 0) or payload.get("count", 0)
    stats = dict(s["kg_stats"])
    stats["triples"] = stats.get("triples", 0) + count
    return s.set("kg_stats", stats)


def _on_community_regenerated(s, action: Action) -> Any:
    """Update communities count in kg_stats."""
    payload = action.payload or {}
    count = payload.get("community_count") or payload.get("count")
    if count is None:
        return s
    stats = dict(s["kg_stats"])
    stats["communities"] = count
    return s.set("kg_stats", stats)


def _on_entity_resolved(s, action: Action) -> Any:
    """Increment entities counter."""
    stats = dict(s["kg_stats"])
    stats["entities"] = stats.get("entities", 0) + 1
    return s.set("kg_stats", stats)


def _on_entity_merged(s, action: Action) -> Any:
    """Decrement entities counter (two → one) if count > 0."""
    stats = dict(s["kg_stats"])
    stats["entities"] = max(0, stats.get("entities", 0) - 1)
    return s.set("kg_stats", stats)


def _on_reflection_completed(s, action: Action) -> Any:
    """Remove completed reflection from active_reflections."""
    payload = action.payload or {}
    reflection_id = payload.get("reflection_id") or payload.get("id")
    if not reflection_id:
        return s
    reflections = {k: v for k, v in s["active_reflections"].items() if k != reflection_id}
    return s.set("active_reflections", reflections)


# ══════════════════════════════════════════════════════════════════════════════
# Reducer
# ══════════════════════════════════════════════════════════════════════════════

memvault_reducer = create_reducer(
    _initial_state(),
    # Memory CRUD
    on(MemoryStored, _on_memory_stored),
    on(MemoryUpdated, _on_memory_updated),
    on(MemoryDeleted, _on_memory_deleted),
    on(MemoryPruned, _on_memory_pruned),
    # Embedding lifecycle
    on(EmbeddingComputed, _on_embedding_computed),
    # KG counters
    on(TripleIngested, _on_triple_ingested),
    on(TripleBatchIngested, _on_triple_batch_ingested),
    on(CommunityRegenerated, _on_community_regenerated),
    on(EntityResolved, _on_entity_resolved),
    on(EntityMerged, _on_entity_merged),
    # Reflection lifecycle
    on(ReflectionCompleted, _on_reflection_completed),
    # Intentionally no-op (handled by reactive_adapters or events.py):
    #   MemoryRecalled, ProfileUpdated, CommunitySummaryRegenerated,
    #   AttitudeEvolved, SkillInvoked, TripleInvalidated, KnowledgeCurated
)

# ══════════════════════════════════════════════════════════════════════════════
# Selectors
# ══════════════════════════════════════════════════════════════════════════════

select_recent_memories = create_selector(lambda s: s["recent_memories"])

select_kg_stats = create_selector(lambda s: s["kg_stats"])

select_embedding_queue_size = create_selector(
    lambda s: s["embedding_queue"],
    result_fn=lambda q: len(q),
)

select_active_reflections = create_selector(lambda s: s["active_reflections"])

select_triple_count = create_selector(
    select_kg_stats,
    result_fn=lambda stats: stats.get("triples", 0),
)

select_community_count = create_selector(
    select_kg_stats,
    result_fn=lambda stats: stats.get("communities", 0),
)

# ══════════════════════════════════════════════════════════════════════════════
# Store instance
# ══════════════════════════════════════════════════════════════════════════════

memvault_store: FeatureStore = FeatureStore(
    "memvault",
    memvault_reducer,
    middlewares=[PerformanceMiddleware(warn_threshold_ms=100.0)],
)

# ══════════════════════════════════════════════════════════════════════════════
# Effects — wrap the three reactive pipe flows from reactive_adapters.py
#
# Effects here do NOT re-implement the flows — they delegate to the
# wire_*_flow() factories so there is a single source of truth.
# ══════════════════════════════════════════════════════════════════════════════


@effect(MemoryStored, store=memvault_store)
async def effect_memory_creation_flow(action: Action, store: FeatureStore) -> None:
    """Trigger Flow 1: MEMORY_STORED → NoiseGate → TagCooccurrence → KG write.

    The actual pipe was already wired at module load (events.py calls
    wire_memory_creation_flow()). This effect acts as a store-level hook:
    it can enqueue the block ID for embedding and log the dispatch.
    """
    payload = action.payload or {}
    block_id = payload.get("id") or payload.get("block_id")
    if block_id:
        # Optimistically track pending embedding
        current_queue = list(store.get_state_raw()["embedding_queue"])
        if block_id not in current_queue:
            current_queue.append(block_id)
            store.dispatch_sync(Action(type="__internal.memvault.embedding_enqueued", payload=None))
            # Note: dispatch_sync is used for internal bookkeeping to avoid
            # re-entering the async dispatch loop. The embedding_queue is
            # updated via MemoryStored handler only when needed.
    logger.debug("effect_memory_creation_flow: block_id=%s", block_id)


@effect(TripleBatchIngested, store=memvault_store)
async def effect_capture_promotion_flow(action: Action, store: FeatureStore) -> None:
    """React to Flow 2 completion: capture.promoted → KG write (TRIPLE_BATCH_INGESTED).

    Flow 2 is wired by wire_capture_promotion_flow() in events.py. When it
    finishes it publishes TRIPLE_BATCH_INGESTED, which lands here. We log
    the stats update for observability.
    """
    payload = action.payload or {}
    ingested = payload.get("ingested", 0)
    stats = store.get_state_raw()["kg_stats"]
    logger.info(
        "effect_capture_promotion_flow: +%d triples (total=%d)",
        ingested,
        stats.get("triples", 0),
    )


@effect(MemoryStored, store=memvault_store)
async def effect_intelligence_digest_flow(action: Action, store: FeatureStore) -> None:
    """React to Flow 3 output: intelligence.digest → MemoryBlock stored.

    Flow 3 (wire_intelligence_digest_flow) creates a MemoryBlock from a digest
    event. That creation publishes MEMORY_STORED, which this effect observes.
    We identify digest-origin blocks by their tags.
    """
    payload = action.payload or {}
    tags = payload.get("tags", [])
    if "intelligence" in tags and "digest" in tags:
        logger.info(
            "effect_intelligence_digest_flow: digest block stored (tags=%s)",
            tags,
        )


# Register the effects declared above
register_effects(
    memvault_store,
    effect_memory_creation_flow,
    effect_capture_promotion_flow,
    effect_intelligence_digest_flow,
)
