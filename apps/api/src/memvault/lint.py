"""Memvault Knowledge Lint — automated knowledge graph health checking.

8 composable checks across 4 layers:
  L0: contradictions, stale, orphan_entities, dangling_refs, community_anomalies, data_gaps
  L1: predicate_contradictions, temporal_staleness, entity_alias_collision
  L3: grounding (action-grounded validation)
  L4: semantic_contradictions (LLM judgment)
  Pipeline: knowledge_conflicts (L1+L3+L4 → cross-validate → cascade)

Cannibalized from 3 converging sources:
- GBrain (Garry Tan) — Knowledge Maintenance/Lint
- Karpathy LLM Wiki — Lint operation
- Harness Engineering — Memory Governance UNVERIFIED→VERIFIED
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .kg_models import Community, EntityCanonical, Triple

logger = logging.getLogger(__name__)

# ======================== Data Structures ========================


@dataclass
class LintFinding:
    check: str  # contradictions | stale | orphan_entities | dangling_refs | ...

    severity: str  # info | warning | error
    entity_id: str
    entity_type: str  # triple | entity | community | system
    message: str
    suggested_action: str  # invalidate | delete | resolve | backfill | none
    metadata: dict = field(default_factory=dict)


@dataclass
class LintReport:
    space_id: str
    checks_run: list[str]
    findings: list[LintFinding]
    summary: dict[str, int]
    run_duration_ms: float
    run_at: datetime


@dataclass
class CandidateConflict:
    """Stage 1 output: a suspected conflict from any detection layer."""

    detection_layer: int  # 1-4
    check_name: str
    entity_type: str  # "triple" | "block" | "attitude"
    entity_id_a: str
    entity_id_b: str | None
    source_session_a: str | None
    source_session_b: str | None
    description: str
    raw_confidence: float
    metadata: dict = field(default_factory=dict)


@dataclass
class ConfirmedConflict:
    """Stage 2 output: cross-validated conflict ready for remediation."""

    candidate: CandidateConflict
    cross_validation_score: float
    evidence: list[str]
    stale_id: str
    fresh_id: str | None
    cascade_targets: list[str] = field(default_factory=list)


# ======================== Check Functions ========================


async def check_contradictions(
    db: AsyncSession,
    space_id: str,
    *,
    sample_size: int = 100,
    similarity_threshold: float = 0.80,
) -> list[LintFinding]:
    """Find valid triples that contradict each other via Qdrant semantic search."""
    from src.shared.embedding import get_embedding
    from src.shared.qdrant_client import is_available as qdrant_available
    from src.shared.qdrant_search import vector_search
    from src.shared.search_types import SearchConfig

    if not await qdrant_available():
        return [
            LintFinding(
                check="contradictions",
                severity="warning",
                entity_id="",
                entity_type="system",
                message="Qdrant unavailable — contradiction check skipped",
                suggested_action="none",
            )
        ]

    # Sample recent valid triples
    q = (
        select(Triple)
        .where(Triple.space_id == space_id, Triple.invalid_at.is_(None))
        .order_by(Triple.created_at.desc())
        .limit(sample_size)
    )
    triples = (await db.execute(q)).scalars().all()
    findings: list[LintFinding] = []
    seen_pairs: set[tuple[str, str]] = set()

    for triple in triples:
        embedding_text = f"{triple.subject} {triple.predicate} {triple.object}"
        embedding = await get_embedding(embedding_text)
        if embedding is None:
            continue

        config = SearchConfig(
            top_k=10,
            score_threshold=similarity_threshold,
            service_ids=["memvault-triple"],
        )
        results = await vector_search(embedding, space_id, config)
        if not results:
            continue

        candidate_ids = [r.entity_id for r in results]
        cq = select(Triple).where(
            Triple.id.in_(candidate_ids),
            Triple.id != triple.id,
            Triple.invalid_at.is_(None),
            Triple.subject == triple.subject,
            Triple.predicate == triple.predicate,
        )
        candidates = (await db.execute(cq)).scalars().all()
        for c in candidates:
            if c.object.strip().lower() == triple.object.strip().lower():
                continue
            pair = tuple(sorted([triple.id, c.id]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            findings.append(
                LintFinding(
                    check="contradictions",
                    severity="warning",
                    entity_id=triple.id,
                    entity_type="triple",
                    message=(
                        f'"{triple.subject} {triple.predicate}" has contradicting objects: '
                        f'"{triple.object}" vs "{c.object}"'
                    ),
                    suggested_action="resolve",
                    metadata={"triple_a": triple.id, "triple_b": c.id},
                )
            )

    return findings


async def check_stale_triples(
    db: AsyncSession,
    space_id: str,
    *,
    days_threshold: int = 90,
    access_threshold: int = 2,
) -> list[LintFinding]:
    """Find valid triples not accessed for a long time with low access count."""
    cutoff = datetime.now(UTC) - timedelta(days=days_threshold)
    q = select(Triple).where(
        Triple.space_id == space_id,
        Triple.invalid_at.is_(None),
        Triple.access_count < access_threshold,
        (
            (Triple.last_accessed_at < cutoff)
            | (Triple.last_accessed_at.is_(None) & (Triple.created_at < cutoff))
        ),
    )
    triples = (await db.execute(q)).scalars().all()
    return [
        LintFinding(
            check="stale",
            severity="info",
            entity_id=t.id,
            entity_type="triple",
            message=(
                f'"{t.subject} {t.predicate} {t.object[:50]}" '
                f"last accessed {t.last_accessed_at or 'never'}, count={t.access_count}"
            ),
            suggested_action="invalidate",
            metadata={
                "last_accessed_at": str(t.last_accessed_at),
                "access_count": t.access_count,
                "created_at": str(t.created_at),
            },
        )
        for t in triples
    ]


async def check_orphan_entities(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Find entities with zero active triples pointing to them."""
    # Subquery: entity IDs referenced by at least one valid triple
    subj_ids = (
        select(Triple.canonical_subject_id)
        .where(Triple.space_id == space_id, Triple.invalid_at.is_(None))
        .distinct()
    )
    obj_ids = (
        select(Triple.canonical_object_id)
        .where(Triple.space_id == space_id, Triple.invalid_at.is_(None))
        .distinct()
    )
    q = select(EntityCanonical).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
        EntityCanonical.id.notin_(subj_ids),
        EntityCanonical.id.notin_(obj_ids),
    )
    orphans = (await db.execute(q)).scalars().all()
    return [
        LintFinding(
            check="orphan_entities",
            severity="info",
            entity_id=e.id,
            entity_type="entity",
            message=f'Entity "{e.canonical_name}" ({e.entity_type}) has no active triples',
            suggested_action="delete",
            metadata={"merge_count": e.merge_count},
        )
        for e in orphans
    ]


async def check_dangling_refs(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Find valid triples missing canonical entity links."""
    q = select(Triple).where(
        Triple.space_id == space_id,
        Triple.invalid_at.is_(None),
        (Triple.canonical_subject_id.is_(None) | Triple.canonical_object_id.is_(None)),
    )
    triples = (await db.execute(q)).scalars().all()
    return [
        LintFinding(
            check="dangling_refs",
            severity="warning",
            entity_id=t.id,
            entity_type="triple",
            message=(
                f'"{t.subject} {t.predicate} {t.object[:50]}" missing canonical link '
                f"(subject={'✗' if not t.canonical_subject_id else '✓'}, "
                f"object={'✗' if not t.canonical_object_id else '✓'})"
            ),
            suggested_action="resolve",
        )
        for t in triples
    ]


async def check_community_anomalies(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Find communities with abnormal size or low modularity."""
    q = select(Community).where(Community.space_id == space_id)
    communities = (await db.execute(q)).scalars().all()
    if len(communities) < 3:
        return []

    sizes = [c.size for c in communities]
    mean_size = statistics.mean(sizes)
    stdev_size = statistics.stdev(sizes) if len(sizes) > 1 else 0
    threshold = mean_size + 2 * stdev_size if stdev_size > 0 else mean_size * 3

    findings: list[LintFinding] = []
    for c in communities:
        issues = []
        if c.size > threshold:
            issues.append(f"size {c.size} > threshold {threshold:.0f}")
        if c.modularity_score is not None and c.modularity_score < 0.1:
            issues.append(f"modularity {c.modularity_score:.3f} < 0.1")
        if issues:
            findings.append(
                LintFinding(
                    check="community_anomalies",
                    severity="info",
                    entity_id=c.id,
                    entity_type="community",
                    message=f'Community "{c.name}" (L{c.resolution_level}): {", ".join(issues)}',
                    suggested_action="none",
                    metadata={
                        "size": c.size,
                        "modularity_score": c.modularity_score,
                        "resolution_level": c.resolution_level,
                    },
                )
            )
    return findings


async def check_data_gaps(
    db: AsyncSession,
    space_id: str,
    *,
    min_merge_count: int = 2,
    max_triples: int = 3,
) -> list[LintFinding]:
    """Find entities that are frequently referenced but have few triples."""
    # Count active triples per entity (as subject or object)
    subj_count = (
        select(
            Triple.canonical_subject_id.label("eid"),
            func.count(Triple.id).label("cnt"),
        )
        .where(Triple.space_id == space_id, Triple.invalid_at.is_(None))
        .group_by(Triple.canonical_subject_id)
        .subquery()
    )
    obj_count = (
        select(
            Triple.canonical_object_id.label("eid"),
            func.count(Triple.id).label("cnt"),
        )
        .where(Triple.space_id == space_id, Triple.invalid_at.is_(None))
        .group_by(Triple.canonical_object_id)
        .subquery()
    )

    q = select(EntityCanonical).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.merge_count >= min_merge_count,
    )
    entities = (await db.execute(q)).scalars().all()

    # Get triple counts per entity
    eid_to_count: dict[str, int] = {}
    for sub in [subj_count, obj_count]:
        rows = (await db.execute(select(sub.c.eid, sub.c.cnt))).all()
        for eid, cnt in rows:
            if eid:
                eid_to_count[eid] = eid_to_count.get(eid, 0) + cnt

    findings: list[LintFinding] = []
    for e in entities:
        triple_count = eid_to_count.get(e.id, 0)
        if triple_count <= max_triples:
            findings.append(
                LintFinding(
                    check="data_gaps",
                    severity="info",
                    entity_id=e.id,
                    entity_type="entity",
                    message=(
                        f'Entity "{e.canonical_name}" has {triple_count} triples '
                        f"but merge_count={e.merge_count} (frequently referenced)"
                    ),
                    suggested_action="backfill",
                    metadata={
                        "triple_count": triple_count,
                        "merge_count": e.merge_count,
                    },
                )
            )
    return findings


# ======================== Semantic Contradiction Check ========================


async def check_semantic_contradictions(
    db: AsyncSession,
    space_id: str,
    *,
    sample_size: int = 50,
    similarity_threshold: float = 0.70,
    max_llm_calls: int = 20,
    verbose: bool = False,
) -> list[LintFinding]:
    """Find semantically related blocks with contradictory or evolved claims via LLM.

    Unlike check_contradictions() which requires exact subject+predicate match,
    this check uses pure embedding similarity (no structural constraint) and
    LLM judgment to detect belief evolution and semantic contradictions.
    """
    from pydantic_ai import Agent as PydanticAgent

    from src.shared.embedding import get_embedding
    from src.shared.qdrant_client import is_available as qdrant_available
    from src.shared.qdrant_search import vector_search
    from src.shared.search_types import SearchConfig

    from .llm_models import SemanticLintOutput
    from .models import MemoryBlock

    if not await qdrant_available():
        return [
            LintFinding(
                check="semantic_contradictions",
                severity="warning",
                entity_id="",
                entity_type="system",
                message="Qdrant unavailable — semantic contradiction check skipped",
                suggested_action="none",
            )
        ]

    # Mixed sampling: half recent + half oldest — ensures cross-era comparison
    half = sample_size // 2
    base_where = [
        MemoryBlock.space_id == space_id,
        MemoryBlock.deleted_at.is_(None),
        MemoryBlock.invalid_at.is_(None),
        MemoryBlock.block_type.in_(["knowledge", "attitude"]),
    ]

    q_recent = (
        select(MemoryBlock).where(*base_where).order_by(MemoryBlock.created_at.desc()).limit(half)
    )
    q_oldest = (
        select(MemoryBlock).where(*base_where).order_by(MemoryBlock.created_at.asc()).limit(half)
    )
    recent = (await db.execute(q_recent)).scalars().all()
    oldest = (await db.execute(q_oldest)).scalars().all()

    # Merge and deduplicate (oldest blocks might overlap with recent in small datasets)
    seen_ids: set[str] = set()
    blocks: list[MemoryBlock] = []
    for b in recent + oldest:
        if b.id not in seen_ids:
            seen_ids.add(b.id)
            blocks.append(b)

    # Fall back to all types if not enough knowledge/attitude blocks
    if len(blocks) < 10:
        q_all = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                MemoryBlock.deleted_at.is_(None),
                MemoryBlock.invalid_at.is_(None),
            )
            .order_by(MemoryBlock.created_at.desc())
            .limit(sample_size)
        )
        blocks = (await db.execute(q_all)).scalars().all()

    if not blocks:
        logger.info("semantic_lint: no valid blocks found for space=%s", space_id)
        return []

    logger.info("semantic_lint: sampled %d blocks for space=%s", len(blocks), space_id)

    # Build block lookup for later
    block_map: dict[str, MemoryBlock] = {b.id: b for b in blocks}

    # Collect candidate pairs via embedding search
    seen_pairs: set[tuple[str, str]] = set()
    candidate_pairs: list[tuple[MemoryBlock, MemoryBlock, float]] = []

    for block in blocks:
        if len(candidate_pairs) >= max_llm_calls:
            break

        content = (block.content or "").strip()
        if len(content) < 20:
            continue

        embedding = await get_embedding(content)
        if embedding is None:
            continue

        config = SearchConfig(
            top_k=5,
            score_threshold=similarity_threshold,
            service_ids=["memvault"],
        )
        results = await vector_search(embedding, space_id, config)

        for r in results:
            if r.entity_id == block.id:
                continue
            pair = tuple(sorted([block.id, r.entity_id]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Look up the other block
            other = block_map.get(r.entity_id)
            if other is None:
                # Not in our sample — fetch from DB
                oq = select(MemoryBlock).where(
                    MemoryBlock.id == r.entity_id,
                    MemoryBlock.deleted_at.is_(None),
                    MemoryBlock.invalid_at.is_(None),
                )
                other = (await db.execute(oq)).scalar_one_or_none()
            if other is None:
                continue

            # Skip pairs from the same source session (not evolution, just co-extracted)
            if (
                block.source_session
                and other.source_session
                and block.source_session == other.source_session
            ):
                continue

            candidate_pairs.append((block, other, r.score))
            if len(candidate_pairs) >= max_llm_calls:
                break

    findings: list[LintFinding] = []

    if not candidate_pairs:
        return findings

    # LLM agent for semantic judgment
    _lint_agent = PydanticAgent(
        output_type=SemanticLintOutput,
        system_prompt=(
            "You are a knowledge graph auditor. Compare two memory blocks from the same "
            "personal knowledge base and classify their relationship.\n\n"
            "Decisions:\n"
            '- "contradiction": The blocks make directly conflicting claims about the same topic.\n'
            '- "evolution": The user\'s belief or situation has changed over time. '
            "The newer block supersedes the older one.\n"
            '- "compatible": The blocks are related but not contradictory — different aspects, '
            "contexts, or complementary information.\n\n"
            "Consider timestamps: a newer block about the same topic likely reflects "
            "the user's current state.\n"
            "Set stale_id to the ID of the outdated block (for evolution/contradiction), "
            "or null if compatible.\n"
            "Be conservative — only flag contradiction/evolution when clearly warranted."
        ),
        retries=1,
    )

    # Resolve model with batch-friendly fallback to avoid rate limits
    from .llm_config import make_litellm_model, resolve_model

    batch_candidates = [
        "kimi-k2.5",
        "deepseek-v3",
        "qwen3.5-flash",
        "grok-4.1-fast",
        "gemini-3.1-flash",
    ]
    model_name = await resolve_model(candidates=batch_candidates)
    model = make_litellm_model(model_name)

    for block_a, block_b, score in candidate_pairs:
        # Determine which is older/newer
        if block_a.created_at and block_b.created_at:
            older = block_a if block_a.created_at < block_b.created_at else block_b
            newer = block_b if block_a.created_at < block_b.created_at else block_a
        else:
            older, newer = block_a, block_b

        user_message = (
            f"OLDER block (ID: {older.id}, type: {older.block_type}, "
            f"created: {older.created_at}):\n{(older.content or '')[:500]}\n\n"
            f"NEWER block (ID: {newer.id}, type: {newer.block_type}, "
            f"created: {newer.created_at}):\n{(newer.content or '')[:500]}\n\n"
            f"Semantic similarity: {score:.3f}\n"
            "Classify the relationship and explain briefly."
        )

        # Rate-limit protection: retry once after backoff on 429
        import asyncio as _asyncio

        output = None
        for attempt in range(2):
            try:
                result = await _lint_agent.run(
                    user_message,
                    model=model,
                    model_settings={"temperature": 0.1, "max_tokens": 256, "timeout": 15},
                )
                output = result.output
                break
            except Exception as exc:
                if attempt == 0 and "429" in str(exc):
                    await _asyncio.sleep(3)
                    continue
                logger.debug(
                    "semantic_lint: LLM failed for pair (%s, %s): %s",
                    block_a.id,
                    block_b.id,
                    exc,
                )
                break

        if output is None:
            continue

        # Pace requests to avoid rate limits
        await _asyncio.sleep(1)

        logger.info(
            "semantic_lint: pair (%s, %s) → %s (confidence=%.2f)",
            block_a.id[:8],
            block_b.id[:8],
            output.relationship,
            output.confidence,
        )

        if output.relationship == "compatible":
            if verbose:
                findings.append(
                    LintFinding(
                        check="semantic_contradictions",
                        severity="info",
                        entity_id=block_a.id,
                        entity_type="block",
                        message=(
                            f"Compatible (confidence={output.confidence:.2f}): {output.reason}"
                        ),
                        suggested_action="none",
                        metadata={
                            "relationship": "compatible",
                            "block_a": block_a.id,
                            "block_b": block_b.id,
                            "similarity": round(score, 3),
                            "content_a": (block_a.content or "")[:100],
                            "content_b": (block_b.content or "")[:100],
                        },
                    )
                )
            continue

        if output.relationship == "evolution":
            stale_id = output.stale_id or older.id
            fresh_id = newer.id if stale_id == older.id else older.id
            findings.append(
                LintFinding(
                    check="semantic_contradictions",
                    severity="warning",
                    entity_id=stale_id,
                    entity_type="block",
                    message=(
                        f"Belief evolution detected (confidence={output.confidence:.2f}): "
                        f"{output.reason}"
                    ),
                    suggested_action="invalidate",
                    metadata={
                        "relationship": "evolution",
                        "stale_id": stale_id,
                        "fresh_id": fresh_id,
                        "similarity": round(score, 3),
                        "confidence": output.confidence,
                    },
                )
            )
        elif output.relationship == "contradiction":
            findings.append(
                LintFinding(
                    check="semantic_contradictions",
                    severity="warning",
                    entity_id=block_a.id,
                    entity_type="block",
                    message=(
                        f"Semantic contradiction (confidence={output.confidence:.2f}): "
                        f"{output.reason}"
                    ),
                    suggested_action="resolve",
                    metadata={
                        "relationship": "contradiction",
                        "block_a": block_a.id,
                        "block_b": block_b.id,
                        "similarity": round(score, 3),
                        "confidence": output.confidence,
                    },
                )
            )

    return findings


# ======================== Layer 1: Graph Structure Checks ========================

# Predicates whose values change over time (volatile state claims)
VOLATILE_PREDICATES = frozenset(
    {
        "pattern_is",
        "flow_is",
        "implemented_as",
        "configured_with",
        "default_is",
        "format_is",
        "chosen_over",
    }
)

# Predicate pairs that are structurally contradictory
# (pred_a, pred_b, mode): "same_pair" = same (S,O), "reverse_pair" = (S,O) vs (O,S)
PREDICATE_CONTRADICTION_RULES: list[tuple[str, str, str]] = [
    ("should", "should_NOT", "same_pair"),
    ("enables", "prevents", "same_pair"),
    ("improves", "degrades", "same_pair"),
    ("fixes", "causes", "same_pair"),
    ("chosen_over", "chosen_over", "reverse_pair"),
]


async def check_predicate_contradictions(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Find valid triples with structurally contradictory predicates for the same entity pair."""
    from sqlalchemy.orm import aliased

    findings: list[LintFinding] = []
    t1 = aliased(Triple)
    t2 = aliased(Triple)

    for pred_a, pred_b, mode in PREDICATE_CONTRADICTION_RULES:
        if mode == "same_pair":
            q = select(t1, t2).where(
                t1.space_id == space_id,
                t2.space_id == space_id,
                t1.invalid_at.is_(None),
                t2.invalid_at.is_(None),
                t1.canonical_subject_id.isnot(None),
                t2.canonical_subject_id.isnot(None),
                t1.canonical_subject_id == t2.canonical_subject_id,
                t1.canonical_object_id == t2.canonical_object_id,
                t1.id < t2.id,
                t1.predicate == pred_a,
                t2.predicate == pred_b,
            )
        else:  # reverse_pair
            q = select(t1, t2).where(
                t1.space_id == space_id,
                t2.space_id == space_id,
                t1.invalid_at.is_(None),
                t2.invalid_at.is_(None),
                t1.canonical_subject_id.isnot(None),
                t2.canonical_subject_id.isnot(None),
                t1.canonical_subject_id == t2.canonical_object_id,
                t1.canonical_object_id == t2.canonical_subject_id,
                t1.id < t2.id,
                t1.predicate == pred_a,
                t2.predicate == pred_b,
            )

        rows = (await db.execute(q)).all()
        for row in rows:
            a, b = row[0], row[1]
            findings.append(
                LintFinding(
                    check="predicate_contradictions",
                    severity="error",
                    entity_id=a.id,
                    entity_type="triple",
                    message=(
                        f'Predicate contradiction: "{a.subject} {a.predicate} {a.object}" '
                        f'vs "{b.subject} {b.predicate} {b.object}" ({mode})'
                    ),
                    suggested_action="resolve",
                    metadata={
                        "triple_a": a.id,
                        "triple_b": b.id,
                        "rule": f"{pred_a} vs {pred_b} ({mode})",
                        "created_a": str(a.created_at),
                        "created_b": str(b.created_at),
                    },
                )
            )

    return findings


async def check_temporal_staleness(
    db: AsyncSession,
    space_id: str,
    *,
    days_threshold: int = 30,
) -> list[LintFinding]:
    """Find same-entity triples with volatile predicates that diverge across time periods."""
    from itertools import groupby as itertools_groupby

    q = (
        select(Triple)
        .where(
            Triple.space_id == space_id,
            Triple.invalid_at.is_(None),
            Triple.canonical_subject_id.isnot(None),
            Triple.predicate.in_(VOLATILE_PREDICATES),
        )
        .order_by(Triple.canonical_subject_id, Triple.predicate, Triple.created_at.desc())
    )
    triples = (await db.execute(q)).scalars().all()

    findings: list[LintFinding] = []
    for _key, group in itertools_groupby(
        triples, key=lambda t: (t.canonical_subject_id, t.predicate)
    ):
        group_list = list(group)
        if len(group_list) < 2:
            continue

        newest = group_list[0]
        newest_ts = newest.valid_at or newest.created_at
        if not newest_ts:
            continue

        for older in group_list[1:]:
            older_ts = older.valid_at or older.created_at
            if not older_ts:
                continue
            # Same object → not a conflict, just duplicate
            if older.object and newest.object and older.object.strip() == newest.object.strip():
                continue
            age = abs((newest_ts - older_ts).days)
            if age >= days_threshold:
                findings.append(
                    LintFinding(
                        check="temporal_staleness",
                        severity="warning",
                        entity_id=older.id,
                        entity_type="triple",
                        message=(
                            f"Temporal drift ({age}d): "
                            f'"{older.subject} {older.predicate}" '
                            f'old="{(older.object or "")[:50]}" '
                            f'vs new="{(newest.object or "")[:50]}"'
                        ),
                        suggested_action="invalidate",
                        metadata={
                            "stale_id": older.id,
                            "fresh_id": newest.id,
                            "age_days": age,
                            "predicate": older.predicate,
                        },
                    )
                )

    return findings



async def check_entity_alias_collision(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Find entity pairs that likely represent the same real-world entity."""
    findings: list[LintFinding] = []
    max_findings = 50  # cap to avoid explosion

    # 4a: Alias array overlap (uses GIN index)
    q_overlap = select(EntityCanonical).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    entities = (await db.execute(q_overlap)).scalars().all()

    # Build name→id index for dedup (skip pairs with identical canonical_name)
    name_to_ids: dict[str, list[str]] = {}
    for e in entities:
        name_to_ids.setdefault(e.canonical_name.lower(), []).append(e.id)

    # Build alias → entity mapping (exclude very short aliases)
    alias_map: dict[str, list[str]] = {}
    for e in entities:
        if e.aliases:
            for alias in e.aliases:
                a_lower = alias.lower()
                if len(a_lower) >= 4:  # skip short aliases
                    alias_map.setdefault(a_lower, []).append(e.id)

    seen_collision: set[tuple[str, str]] = set()
    entity_by_id = {e.id: e for e in entities}

    for _alias, eids in alias_map.items():
        if len(eids) < 2 or len(findings) >= max_findings:
            continue
        for i, eid_a in enumerate(eids):
            if len(findings) >= max_findings:
                break
            for eid_b in eids[i + 1 :]:
                pair = tuple(sorted([eid_a, eid_b]))
                if pair in seen_collision:
                    continue
                seen_collision.add(pair)
                ea = entity_by_id.get(eid_a)
                eb = entity_by_id.get(eid_b)
                if not ea or not eb:
                    continue
                # Skip same-name entities (that's entity resolution, not lint)
                if ea.canonical_name.lower() == eb.canonical_name.lower():
                    continue
                findings.append(
                    LintFinding(
                        check="entity_alias_collision",
                        severity="warning",
                        entity_id=eid_a,
                        entity_type="entity",
                        message=(
                            f'Alias collision: "{ea.canonical_name}" and '
                            f'"{eb.canonical_name}" share alias "{_alias}"'
                        ),
                        suggested_action="resolve",
                        metadata={
                            "entity_a": eid_a,
                            "entity_b": eid_b,
                            "shared_alias": _alias,
                        },
                    )
                )

    # 4b: Canonical name substring containment (same entity_type, cap at max_findings)
    for i, e1 in enumerate(entities):
        if len(findings) >= max_findings:
            break
        n1 = e1.canonical_name.lower()
        if len(n1) < 6:
            continue
        for e2 in entities[i + 1 :]:
            n2 = e2.canonical_name.lower()
            if len(n2) < 6:
                continue
            if e1.entity_type != e2.entity_type:
                continue
            pair = tuple(sorted([e1.id, e2.id]))
            if pair in seen_collision:
                continue
            if n1 in n2 or n2 in n1:
                seen_collision.add(pair)
                findings.append(
                    LintFinding(
                        check="entity_alias_collision",
                        severity="info",
                        entity_id=e1.id,
                        entity_type="entity",
                        message=(
                            f'Name containment: "{e1.canonical_name}" ⊂ "{e2.canonical_name}" '
                            f"(type={e1.entity_type})"
                        ),
                        suggested_action="resolve",
                        metadata={
                            "entity_a": e1.id,
                            "entity_b": e2.id,
                            "detection": "name_containment",
                        },
                    )
                )

    return findings


# ======================== Layer 3: Action-Grounded Validation ========================


async def check_grounding(
    db: AsyncSession,
    space_id: str,
) -> list[LintFinding]:
    """Validate knowledge claims against actual system state (ports, modules, names)."""
    from .ground_truth import (
        build_ground_truth,
        check_deprecated_reference,
        check_module_count_claim,
        check_port_claim,
        is_groundable,
    )

    truth = build_ground_truth()
    findings: list[LintFinding] = []

    # Query triples with groundable predicates
    q = (
        select(Triple)
        .where(
            Triple.space_id == space_id,
            Triple.invalid_at.is_(None),
        )
        .limit(1000)
    )
    triples = (await db.execute(q)).scalars().all()

    for t in triples:
        text = f"{t.subject} {t.predicate} {t.object}"

        # Check deprecated names in ANY triple
        dep = check_deprecated_reference(text, truth)
        if dep:
            findings.append(
                LintFinding(
                    check="grounding",
                    severity="error",
                    entity_id=t.id,
                    entity_type="triple",
                    message=f'Deprecated reference "{dep}" in: {text[:80]}',
                    suggested_action="invalidate",
                    metadata={
                        "grounding_category": "deprecated",
                        "deprecated_name": dep,
                    },
                )
            )
            continue

        # Only check groundable predicates for port/module drift
        if not is_groundable(t.predicate):
            continue

        # Port drift
        port_drift = check_port_claim(text, truth)
        if port_drift:
            claimed, actual = port_drift
            findings.append(
                LintFinding(
                    check="grounding",
                    severity="error",
                    entity_id=t.id,
                    entity_type="triple",
                    message=(
                        f"Port drift: claimed {claimed}, actual {actual} (from port_registry)"
                    ),
                    suggested_action="invalidate",
                    metadata={
                        "grounding_category": "port",
                        "claimed": str(claimed),
                        "actual": str(actual),
                    },
                )
            )

        # Module count drift
        count_drift = check_module_count_claim(text, truth)
        if count_drift:
            claimed, actual = count_drift
            findings.append(
                LintFinding(
                    check="grounding",
                    severity="warning",
                    entity_id=t.id,
                    entity_type="triple",
                    message=(f"Module count drift: claimed {claimed}, actual {actual}"),
                    suggested_action="invalidate",
                    metadata={
                        "grounding_category": "module_count",
                        "claimed": str(claimed),
                        "actual": str(actual),
                    },
                )
            )

    return findings


# ======================== Knowledge Conflict Pipeline ========================


def _finding_to_candidate(finding: LintFinding, detection_layer: int) -> CandidateConflict | None:
    """Convert a LintFinding into a CandidateConflict."""
    meta = finding.metadata
    check = finding.check

    if check == "predicate_contradictions":
        eid_a = meta.get("triple_a", finding.entity_id)
        eid_b = meta.get("triple_b")
        confidence = 0.9
    elif check == "temporal_staleness":
        eid_a = meta.get("stale_id", finding.entity_id)
        eid_b = meta.get("fresh_id")
        confidence = 0.7
    elif check == "grounding":
        eid_a = finding.entity_id
        eid_b = None
        confidence = 1.0
    elif check == "semantic_contradictions":
        rel = meta.get("relationship")
        if rel == "evolution":
            eid_a = meta.get("stale_id", finding.entity_id)
            eid_b = meta.get("fresh_id")
        elif rel == "contradiction":
            eid_a = meta.get("block_a", finding.entity_id)
            eid_b = meta.get("block_b")
        else:
            return None
        confidence = meta.get("confidence", 0.6)
    else:
        return None

    if not eid_a:
        return None

    return CandidateConflict(
        detection_layer=detection_layer,
        check_name=check,
        entity_type=finding.entity_type,
        entity_id_a=eid_a,
        entity_id_b=eid_b,
        source_session_a=None,
        source_session_b=None,
        description=finding.message,
        raw_confidence=confidence,
        metadata=meta,
    )


async def _cross_validate(
    db: AsyncSession,
    space_id: str,
    candidates: list[CandidateConflict],
) -> list[ConfirmedConflict]:
    """Stage 2: Cross-validate candidates via pincer approach (上下夾擊).

    Triple candidate → trace DOWN to source blocks (via source_session).
    Block candidate → trace UP to derived triples (via source_session).
    Grounding candidate → skip validation (confidence=1.0).
    Gate: cross_validation_score >= 0.6.
    """
    from .models import MemoryBlock

    if not candidates:
        return []

    # --- Batch-fetch source_sessions for all referenced entities ---
    triple_ids: set[str] = set()
    block_ids: set[str] = set()
    for c in candidates:
        if c.entity_type == "triple":
            triple_ids.add(c.entity_id_a)
            if c.entity_id_b:
                triple_ids.add(c.entity_id_b)
        elif c.entity_type == "block":
            block_ids.add(c.entity_id_a)
            if c.entity_id_b:
                block_ids.add(c.entity_id_b)

    triple_sessions: dict[str, str | None] = {}
    if triple_ids:
        q = select(Triple.id, Triple.source_session).where(Triple.id.in_(triple_ids))
        triple_sessions = {r[0]: r[1] for r in (await db.execute(q)).all()}

    block_sessions: dict[str, str | None] = {}
    if block_ids:
        q = select(MemoryBlock.id, MemoryBlock.source_session).where(MemoryBlock.id.in_(block_ids))
        block_sessions = {r[0]: r[1] for r in (await db.execute(q)).all()}

    # Populate source_sessions on candidates
    for c in candidates:
        if c.entity_type == "triple":
            c.source_session_a = triple_sessions.get(c.entity_id_a)
            if c.entity_id_b:
                c.source_session_b = triple_sessions.get(c.entity_id_b)
        elif c.entity_type == "block":
            c.source_session_a = block_sessions.get(c.entity_id_a)
            if c.entity_id_b:
                c.source_session_b = block_sessions.get(c.entity_id_b)

    # --- Batch-fetch session cross-references ---
    all_sessions: set[str] = set()
    for c in candidates:
        if c.source_session_a:
            all_sessions.add(c.source_session_a)
        if c.source_session_b:
            all_sessions.add(c.source_session_b)

    session_block_ids: dict[str, set[str]] = {}
    session_triple_ids: dict[str, set[str]] = {}
    if all_sessions:
        bq = select(MemoryBlock.id, MemoryBlock.source_session).where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.source_session.in_(all_sessions),
            MemoryBlock.deleted_at.is_(None),
        )
        for bid, sess in (await db.execute(bq)).all():
            session_block_ids.setdefault(sess, set()).add(bid)

        tq = select(Triple.id, Triple.source_session).where(
            Triple.space_id == space_id,
            Triple.source_session.in_(all_sessions),
            Triple.invalid_at.is_(None),
        )
        for tid, sess in (await db.execute(tq)).all():
            session_triple_ids.setdefault(sess, set()).add(tid)

    # Build L1/L4 cross-reference sets
    l1_triple_ids: set[str] = set()
    l4_block_ids: set[str] = set()
    for c in candidates:
        if c.detection_layer == 1 and c.entity_type == "triple":
            l1_triple_ids.add(c.entity_id_a)
            if c.entity_id_b:
                l1_triple_ids.add(c.entity_id_b)
        elif c.detection_layer == 4 and c.entity_type == "block":
            l4_block_ids.add(c.entity_id_a)
            if c.entity_id_b:
                l4_block_ids.add(c.entity_id_b)

    # --- Score each candidate ---
    confirmed: list[ConfirmedConflict] = []
    for c in candidates:
        evidence: list[str] = []
        score = c.raw_confidence
        stale_id = c.entity_id_a
        fresh_id = c.entity_id_b

        if c.detection_layer == 3:
            # Grounding = absolute truth, skip validation
            score = 1.0
            evidence.append("Ground truth verified (system state)")
            fresh_id = None

        elif c.entity_type == "triple":
            # Triple → trace DOWN to source blocks
            sess = c.source_session_a
            if sess:
                b_ids = session_block_ids.get(sess, set())
                if b_ids:
                    evidence.append(f"Source session has {len(b_ids)} blocks")
                    score += 0.1
                    flagged = b_ids & l4_block_ids
                    if flagged:
                        score += 0.2
                        evidence.append(f"{len(flagged)} source blocks also flagged by L4")
            stale_id = c.metadata.get("stale_id", c.entity_id_a)
            fresh_id = c.metadata.get("fresh_id", c.entity_id_b)

        elif c.entity_type == "block":
            # Block → trace UP to derived triples
            sess = c.source_session_a
            if sess:
                t_ids = session_triple_ids.get(sess, set())
                if t_ids:
                    evidence.append(f"Session has {len(t_ids)} active triples")
                    flagged = t_ids & l1_triple_ids
                    if flagged:
                        score += 0.2
                        evidence.append(f"{len(flagged)} derived triples flagged by L1")
            stale_id = c.metadata.get("stale_id", c.entity_id_a)
            fresh_id = c.metadata.get("fresh_id", c.entity_id_b)

        elif c.entity_type == "attitude":
            evidence.append(f"Attitude chain issue: {c.metadata.get('issue', 'unknown')}")

        score = min(score, 1.0)
        if score < 0.6:
            continue

        confirmed.append(
            ConfirmedConflict(
                candidate=c,
                cross_validation_score=round(score, 3),
                evidence=evidence,
                stale_id=stale_id,
                fresh_id=fresh_id,
            )
        )

    return confirmed


async def check_knowledge_conflicts(
    db: AsyncSession,
    space_id: str,
    *,
    max_llm_calls: int = 20,
) -> list[LintFinding]:
    """Unified knowledge conflict pipeline (Stage 1 + Stage 2).

    Stage 1: Integrate L1 (graph) + L3 (grounding) + L4 (semantic LLM) candidates.
    Stage 2: Cross-validate via pincer approach (上下夾擊).
    Returns confirmed conflicts as LintFindings with enriched metadata.
    """
    candidates: list[CandidateConflict] = []
    seen_pairs: set[tuple[str, str]] = set()

    def _dedup_add(results: list[LintFinding], layer: int) -> None:
        for f in results:
            c = _finding_to_candidate(f, detection_layer=layer)
            if c is None:
                continue
            pair = tuple(sorted([c.entity_id_a, c.entity_id_b or ""]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            candidates.append(c)

    # L1: Graph structure (~200ms, deterministic)
    for check_fn in [
        check_predicate_contradictions,
        check_temporal_staleness,
    ]:
        try:
            _dedup_add(await check_fn(db, space_id), layer=1)
        except Exception as exc:
            logger.warning("knowledge_conflicts L1 %s: %s", check_fn.__name__, exc)

    # L3: Grounding (~15ms, deterministic — triples + attitudes)
    try:
        _dedup_add(await check_grounding(db, space_id), layer=3)
    except Exception as exc:
        logger.warning("knowledge_conflicts L3: %s", exc)

    # L4: Semantic LLM (slow, ≤max_llm_calls — blocks + attitudes)
    half_llm = max(max_llm_calls // 2, 5)
    try:
        _dedup_add(
            await check_semantic_contradictions(db, space_id, max_llm_calls=half_llm),
            layer=4,
        )
    except Exception as exc:
        logger.warning("knowledge_conflicts L4 blocks: %s", exc)

    logger.info("knowledge_conflicts: %d candidates from L1+L3+L4", len(candidates))

    # Stage 2: Cross-validate
    confirmed = await _cross_validate(db, space_id, candidates)
    logger.info(
        "knowledge_conflicts: %d/%d confirmed",
        len(confirmed),
        len(candidates),
    )

    # Convert to LintFindings with enriched metadata
    return [
        LintFinding(
            check="knowledge_conflicts",
            severity="error" if c.cross_validation_score >= 0.8 else "warning",
            entity_id=c.stale_id,
            entity_type=c.candidate.entity_type,
            message=(
                f"[L{c.candidate.detection_layer}/{c.candidate.check_name}] "
                f"{c.candidate.description}"
            ),
            suggested_action="invalidate",
            metadata={
                "detection_layer": c.candidate.detection_layer,
                "original_check": c.candidate.check_name,
                "cross_validation_score": c.cross_validation_score,
                "evidence": c.evidence,
                "stale_id": c.stale_id,
                "fresh_id": c.fresh_id,
                "entity_id_a": c.candidate.entity_id_a,
                "entity_id_b": c.candidate.entity_id_b,
                "source_session_a": c.candidate.source_session_a,
                "source_session_b": c.candidate.source_session_b,
            },
        )
        for c in confirmed
    ]


# ======================== Runner ========================

ALL_CHECKS: dict[str, object] = {
    # Original checks
    "contradictions": check_contradictions,
    "semantic_contradictions": check_semantic_contradictions,
    "stale": check_stale_triples,
    "orphan_entities": check_orphan_entities,
    "dangling_refs": check_dangling_refs,
    "community_anomalies": check_community_anomalies,
    "data_gaps": check_data_gaps,
    # Layer 1: Graph structure (deterministic, fast)
    "predicate_contradictions": check_predicate_contradictions,
    "temporal_staleness": check_temporal_staleness,
    "entity_alias_collision": check_entity_alias_collision,
    # Layer 3: Action-grounded validation
    "grounding": check_grounding,
    # Unified pipeline: L1+L3+L4 → cross-validate → cascade
    "knowledge_conflicts": check_knowledge_conflicts,
}

FAST_CHECKS = [
    "stale",
    "orphan_entities",
    "dangling_refs",
    "data_gaps",
    "predicate_contradictions",
    "temporal_staleness",
    "entity_alias_collision",
]


async def _run_lint_sequential(
    db: AsyncSession,
    space_id: str,
    checks: list[str] | None,
) -> LintReport:
    """Run knowledge lint checks sequentially. If checks is None, run all."""
    selected = checks or list(ALL_CHECKS.keys())
    findings: list[LintFinding] = []
    start = time.monotonic()

    for name in selected:
        check_fn = ALL_CHECKS.get(name)
        if check_fn is None:
            continue
        try:
            results = await check_fn(db, space_id)
            findings.extend(results)
        except Exception as e:
            findings.append(
                LintFinding(
                    check=name,
                    severity="error",
                    entity_id="",
                    entity_type="system",
                    message=f"Check failed: {e}",
                    suggested_action="none",
                )
            )

    elapsed = (time.monotonic() - start) * 1000
    summary: dict[str, int] = {}
    for f in findings:
        summary[f.check] = summary.get(f.check, 0) + 1

    return LintReport(
        space_id=space_id,
        checks_run=selected,
        findings=findings,
        summary=summary,
        run_duration_ms=elapsed,
        run_at=datetime.now(UTC),
    )


async def _run_lint_pipeline(
    db: AsyncSession,
    space_id: str,
    checks: list[str] | None,
) -> LintReport:
    """Run lint via Reactive Pipeline (parallel execution)."""
    from .pipeline_config import MemvaultPipelineConfig
    from .pipelines.lint_pipeline import _CHECK_REGISTRY, build_lint_pipeline

    config = MemvaultPipelineConfig.from_env()

    # Check if all requested checks are in the pipeline registry
    if checks:
        registry_names = {name for name, _, _ in _CHECK_REGISTRY}
        unsupported = set(checks) - registry_names
        if unsupported:
            logger.warning(
                "lint pipeline: checks %s not in registry, falling back to sequential",
                unsupported,
            )
            return await _run_lint_sequential(db, space_id, checks)

    pipeline = build_lint_pipeline(checks=checks, config=config)

    t0 = time.time()
    ctx = await pipeline.execute({"db": db, "space_id": space_id})
    elapsed = (time.time() - t0) * 1000

    findings = ctx.get("findings", [])
    meta = ctx.get("_pipeline_meta")

    checks_run = []
    if meta:
        checks_run = [s.removeprefix("lint.") for s in meta.stages_applied]

    summary: dict[str, int] = {}
    for f in findings:
        summary[f.check] = summary.get(f.check, 0) + 1

    return LintReport(
        space_id=space_id,
        checks_run=checks_run,
        findings=findings,
        summary=summary,
        run_duration_ms=meta.total_ms if meta else elapsed,
        run_at=datetime.now(UTC),
    )


# ======================== Knowledge Lint v2 (Task 9) — Wiki-Lint 10 Checks ========================


async def run_health_check(
    db: AsyncSession,
    space_id: str = "default",
    *,
    only: list[str] | None = None,
) -> LintReport:
    """Run the 10 wiki-lint inspired checks (orphan blocks, dead triples, …).

    Distinct from `run_lint` (which dispatches the L0..L4 graph pipeline). Each
    check is read-only and returns a list of LintFinding. Soft-deleted blocks /
    invalid triples are excluded by every individual check.
    """
    from .lint_checks import WIKI_LINT_CHECKS

    selected = [
        (name, fn, default_sev)
        for name, fn, default_sev in WIKI_LINT_CHECKS
        if only is None or name in only
    ]
    findings: list[LintFinding] = []
    start = time.monotonic()

    for name, fn, _default_sev in selected:
        try:
            results = await fn(db, space_id)
            findings.extend(results)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("run_health_check: %s failed: %s", name, exc)
            findings.append(
                LintFinding(
                    check=name,
                    severity="error",
                    entity_id="",
                    entity_type="system",
                    message=f"Check {name} failed: {exc}",
                    suggested_action="Investigate; this is a lint runner bug.",
                    metadata={"error": str(exc)},
                )
            )

    elapsed = (time.monotonic() - start) * 1000
    summary: dict[str, int] = {}
    for f in findings:
        summary[f.check] = summary.get(f.check, 0) + 1

    return LintReport(
        space_id=space_id,
        checks_run=[name for name, _, _ in selected],
        findings=findings,
        summary=summary,
        run_duration_ms=elapsed,
        run_at=datetime.now(UTC),
    )


# Map LintFinding.severity (info|warning|error) → wiki-lint bucket
_SEVERITY_BUCKETS: dict[str, str] = {
    "error": "critical",
    "warning": "warning",
    "info": "suggestion",
}


def format_health_report_markdown(report: LintReport) -> str:
    """Render a LintReport as the wiki-lint markdown layout.

    Sections: Summary / Critical / Warnings / Suggestions.
    """
    buckets: dict[str, list[LintFinding]] = {
        "critical": [],
        "warning": [],
        "suggestion": [],
    }
    for f in report.findings:
        bucket = _SEVERITY_BUCKETS.get(f.severity, "suggestion")
        buckets[bucket].append(f)

    date_str = report.run_at.strftime("%Y-%m-%d")
    issues_total = sum(len(v) for v in buckets.values())

    lines: list[str] = [
        f"# Memvault Lint Report — {date_str}",
        "",
        "## Summary",
        f"- Space: `{report.space_id}`",
        f"- Checks run: {', '.join(report.checks_run) or '(none)'}",
        f"- Duration: {report.run_duration_ms:.0f}ms",
        (
            f"- Issues: {issues_total} "
            f"({len(buckets['critical'])} critical / "
            f"{len(buckets['warning'])} warning / "
            f"{len(buckets['suggestion'])} suggestion)"
        ),
        "",
    ]

    for key, title in [
        ("critical", "## Critical"),
        ("warning", "## Warnings"),
        ("suggestion", "## Suggestions"),
    ]:
        items = buckets[key]
        lines.append(title)
        if not items:
            lines.append("_None_")
            lines.append("")
            continue
        for f in items:
            ref = f.entity_id or "-"
            lines.append(f"- [{f.check}] {f.message} — `{ref}`")
            if f.suggested_action and f.suggested_action != "none":
                lines.append(f"    - suggestion: {f.suggested_action}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def run_lint(
    db: AsyncSession,
    space_id: str = "default",
    checks: list[str] | None = None,
    use_pipeline: bool = False,
) -> LintReport:
    """Run knowledge lint checks. If checks is None, run all.

    Args:
        db: Database session.
        space_id: Space to lint.
        checks: Which checks to run. None means all.
        use_pipeline: If True, use the Reactive Pipeline (parallel execution).
                      Falls back to sequential if unsupported checks are requested.
    """
    if use_pipeline:
        return await _run_lint_pipeline(db, space_id, checks)
    return await _run_lint_sequential(db, space_id, checks)


# ======================== Remediation ========================


async def remediate_stale(
    db: AsyncSession,
    findings: list[LintFinding],
    *,
    dry_run: bool = True,
) -> int:
    """Invalidate stale triples. dry_run=True by default (report only)."""
    count = 0
    for f in findings:
        if f.check != "stale" or not f.entity_id:
            continue
        if dry_run:
            continue
        await db.execute(
            update(Triple)
            .where(Triple.id == f.entity_id)
            .values(invalid_at=datetime.now(UTC), invalidation_reason="stale")
        )
        count += 1
    if count > 0:
        await db.commit()
    return count


async def remediate_orphans(
    db: AsyncSession,
    findings: list[LintFinding],
    *,
    dry_run: bool = True,
) -> int:
    """Delete orphan entities. dry_run=True by default (report only)."""
    count = 0
    for f in findings:
        if f.check != "orphan_entities" or not f.entity_id:
            continue
        if dry_run:
            continue
        await db.execute(delete(EntityCanonical).where(EntityCanonical.id == f.entity_id))
        count += 1
    if count > 0:
        await db.commit()
    return count


async def remediate_semantic(
    db: AsyncSession,
    findings: list[LintFinding],
    *,
    dry_run: bool = True,
) -> int:
    """Remediate semantic contradiction findings. dry_run=True by default.

    - evolution: invalidate the stale block (reason="evolved")
    - contradiction: use conflict_resolver for MERGE/SUPERSEDE/COEXIST
    """
    from .models import MemoryBlock

    count = 0
    for f in findings:
        if f.check != "semantic_contradictions" or not f.entity_id:
            continue
        if dry_run:
            continue

        meta = f.metadata
        relationship = meta.get("relationship")

        if relationship == "evolution":
            stale_id = meta.get("stale_id")
            fresh_id = meta.get("fresh_id")
            if not stale_id:
                continue
            await db.execute(
                update(MemoryBlock)
                .where(MemoryBlock.id == stale_id, MemoryBlock.invalid_at.is_(None))
                .values(
                    invalid_at=datetime.now(UTC),
                    superseded_by=fresh_id,
                    invalidation_reason="evolved",
                )
            )
            count += 1

        elif relationship == "contradiction":
            block_a_id = meta.get("block_a")
            block_b_id = meta.get("block_b")
            if not block_a_id or not block_b_id:
                continue

            # Fetch both blocks for conflict resolution
            qa = select(MemoryBlock).where(MemoryBlock.id == block_a_id)
            qb = select(MemoryBlock).where(MemoryBlock.id == block_b_id)
            block_a = (await db.execute(qa)).scalar_one_or_none()
            block_b = (await db.execute(qb)).scalar_one_or_none()
            if not block_a or not block_b:
                continue

            # Determine older/newer for conflict resolution
            if block_a.created_at and block_b.created_at:
                if block_a.created_at < block_b.created_at:
                    existing, newer = block_a, block_b
                else:
                    existing, newer = block_b, block_a
            else:
                existing, newer = block_a, block_b

            try:
                from .conflict_resolver import resolve_conflict

                result = await resolve_conflict(
                    new_content=newer.content or "",
                    existing_content=existing.content or "",
                    existing_block_id=existing.id,
                    block_type=existing.block_type or "knowledge",
                    similarity=meta.get("similarity", 0.0),
                    existing_created_at=str(existing.created_at) if existing.created_at else None,
                )
            except Exception as exc:
                logger.debug(
                    "remediate_semantic: conflict resolution failed for %s: %s", existing.id, exc
                )
                continue

            from src.shared.conflict import ConflictDecision

            if result.decision == ConflictDecision.SUPERSEDE:
                await db.execute(
                    update(MemoryBlock)
                    .where(MemoryBlock.id == existing.id, MemoryBlock.invalid_at.is_(None))
                    .values(
                        invalid_at=datetime.now(UTC),
                        superseded_by=newer.id,
                        invalidation_reason="contradiction",
                    )
                )
                count += 1
            elif result.decision == ConflictDecision.MERGE and result.merged_content:
                # Update newer block with merged content, invalidate older
                await db.execute(
                    update(MemoryBlock)
                    .where(MemoryBlock.id == newer.id)
                    .values(content=result.merged_content)
                )
                await db.execute(
                    update(MemoryBlock)
                    .where(MemoryBlock.id == existing.id, MemoryBlock.invalid_at.is_(None))
                    .values(
                        invalid_at=datetime.now(UTC),
                        superseded_by=newer.id,
                        invalidation_reason="merged",
                    )
                )
                count += 1
            # COEXIST → no action

    if count > 0:
        await db.commit()
    return count


async def remediate_knowledge_conflicts(
    db: AsyncSession,
    findings: list[LintFinding],
    *,
    dry_run: bool = True,
) -> int:
    """Stage 3: Cascade invalidation for confirmed knowledge conflicts.

    Block stale → cascade to same-session triples (content overlap >= 3 words).
    Triple stale → invalidate triple only (conservative: don't touch source block).
    One db.commit() at the end.
    """
    from .models import MemoryBlock

    count = 0
    now = datetime.now(UTC)

    for f in findings:
        if f.check != "knowledge_conflicts" or not f.entity_id:
            continue
        if dry_run:
            continue

        meta = f.metadata
        stale_id = meta.get("stale_id")
        fresh_id = meta.get("fresh_id")
        source_session = meta.get("source_session_a")

        if not stale_id:
            continue

        reason = meta.get("original_check", "knowledge_conflict")

        if f.entity_type == "triple":
            await db.execute(
                update(Triple)
                .where(Triple.id == stale_id, Triple.invalid_at.is_(None))
                .values(
                    invalid_at=now,
                    invalidated_by=fresh_id,
                    invalidation_reason=reason,
                )
            )
            count += 1

        elif f.entity_type == "block":
            await db.execute(
                update(MemoryBlock)
                .where(MemoryBlock.id == stale_id, MemoryBlock.invalid_at.is_(None))
                .values(
                    invalid_at=now,
                    superseded_by=fresh_id,
                    invalidation_reason=reason,
                )
            )
            count += 1

            # Cascade DOWN: degrade confidence of same-session triples
            # (TMS-style: don't invalidate, just reduce trust — 寧漏勿殺)
            if source_session:
                bq = select(MemoryBlock.content).where(MemoryBlock.id == stale_id)
                block_content = (await db.execute(bq)).scalar_one_or_none()
                if block_content:
                    block_words = set(block_content.lower().split())
                    tq = select(Triple).where(
                        Triple.source_session == source_session,
                        Triple.invalid_at.is_(None),
                    )
                    for t in (await db.execute(tq)).scalars().all():
                        t_words = set(f"{t.subject} {t.predicate} {t.object}".lower().split())
                        if len(block_words & t_words) >= 3:
                            # Halve access_count as confidence signal
                            new_count = max(0, (t.access_count or 0) // 2)
                            await db.execute(
                                update(Triple)
                                .where(Triple.id == t.id)
                                .values(access_count=new_count)
                            )
                            count += 1

    if count > 0:
        await db.commit()
    return count


