"""Event type constants — `{domain}.{entity}.{past_tense}`.

Mirrors monorepo's `src.events.types`. memvault's own enum is complete; other
modules' enums are kept for import compatibility but their handlers are
no-op in the OS build (memvault-os doesn't subscribe to them).
"""

from __future__ import annotations


class MemvaultEvents:
    MEMORY_STORED = "memvault.memory.stored"
    MEMORY_UPDATED = "memvault.memory.updated"
    MEMORY_DELETED = "memvault.memory.deleted"
    MEMORY_RECALLED = "memvault.memory.recalled"
    MEMORY_PRUNED = "memvault.memory.pruned"
    EMBEDDING_COMPUTED = "memvault.embedding.computed"
    PROFILE_UPDATED = "memvault.profile.updated"
    # KG events
    TRIPLE_INGESTED = "memvault.triple.ingested"
    TRIPLE_BATCH_INGESTED = "memvault.triple.batch_ingested"
    COMMUNITY_REGENERATED = "memvault.community.regenerated"
    COMMUNITY_SUMMARY_REGENERATED = "memvault.community_summary.regenerated"
    ATTITUDE_EVOLVED = "memvault.attitude.evolved"
    SKILL_INVOKED = "memvault.skill.invoked"
    TRIPLE_INVALIDATED = "memvault.triple.invalidated"
    ENTITY_RESOLVED = "memvault.entity.resolved"
    ENTITY_MERGED = "memvault.entity.merged"
    REFLECTION_COMPLETED = "memvault.reflection.completed"
    KNOWLEDGE_CURATED = "memvault.knowledge.curated"
    # Slow Thinker prefetch trigger
    QUERY_COMPLETED = "memvault.query.completed"
    # Dream Loop consolidation
    DREAM_COMPLETED = "memvault.dream.completed"


# stub: external module event, no-op handler in memvault-os
class SessionIntelligenceEvents:
    DIGEST_COMPLETED = "intelligence.digest.completed"
    PATTERN_DISCOVERED = "intelligence.pattern.discovered"
    TREND_DETECTED = "intelligence.trend.detected"


# stub: external module event, no-op handler in memvault-os
class CaptureEvents:
    CREATED = "capture.created"
    ENRICHED = "capture.enriched"
    PROMOTED = "capture.promoted"
    EXPIRED = "capture.expired"


# stub: external module event, no-op handler in memvault-os
class AuthEvents:
    USER_REGISTERED = "auth.user.registered"
    USER_LOGGED_IN = "auth.user.logged_in"
    USER_LOGGED_OUT = "auth.user.logged_out"
    USER_STATUS_CHANGED = "auth.user.status_changed"
    SESSION_CREATED = "auth.session.created"
    SESSION_REVOKED = "auth.session.revoked"


# stub: external module event, no-op handler in memvault-os
class IdeagraphEvents:
    SPARK_CAPTURED = "ideagraph.spark.captured"
    SPARK_REFINED = "ideagraph.spark.refined"
    LINK_SUGGESTED = "ideagraph.link.suggested"
    LINK_VERIFIED = "ideagraph.link.verified"


# stub: external module event, no-op handler in memvault-os
class TaskflowEvents:
    TASK_CREATED = "taskflow.task.created"
    TASK_UPDATED = "taskflow.task.updated"
    TASK_COMPLETED = "taskflow.task.completed"
    TASK_DELETED = "taskflow.task.deleted"


# stub: external module event, no-op handler in memvault-os
class DocvaultEvents:
    DOCUMENT_CREATED = "docvault.document.created"
    DOCUMENT_INDEXED = "docvault.document.indexed"
    QA_EXECUTED = "docvault.qa.executed"


# stub: external module event, no-op handler in memvault-os
class SearchIndexEvents:
    INDEX_STARTED = "search.index.started"
    INDEX_COMPLETED = "search.index.completed"
    INDEX_FAILED = "search.index.failed"
    BACKFILL_STARTED = "search.backfill.started"
    BACKFILL_COMPLETED = "search.backfill.completed"


# stub: external module event, no-op handler in memvault-os
class SystemEvents:
    HEALTH_CHECKED = "system.health.checked"
    CONFIG_CHANGED = "system.config.changed"


# stub: external module event, no-op handler in memvault-os
class CompletionEvents:
    TASK_DISPATCHED = "completion.task.dispatched"
    TASK_RUNNING = "completion.task.running"
    TASK_COMPLETED = "completion.task.completed"
    TASK_FAILED = "completion.task.failed"
    TASK_TIMEOUT = "completion.task.timeout"
