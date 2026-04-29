"""Memvault KG routes — Knowledge Graph API endpoints.

Prefix: /api/memvault/kg (mounted via __init__.py)
"""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.events_stub.bus import Event, event_bus
# TODO(memvault-os): src.shared.* imports still point at monorepo; extract shared/ in a follow-up task.
from src.shared import qdrant_search
from src.shared.deps import get_db, require_permission
from src.shared.errors import ForbiddenError, NotFoundError
from src.shared.schemas import PaginatedResponse, PaginationParams
from src.shared.search_types import IndexDocument

from .embedding import get_embedding

logger = logging.getLogger(__name__)
from .entity_resolution import entity_resolution_service, normalize_entity_text
from .interest_profile import interest_profile_service
from .kg_schemas import (
    CascadeRecallResult,
    CommunityDetail,
    CommunityRegenerateRequest,
    CommunityResponse,
    CommunitySummaryRegenerateRequest,
    CommunitySummaryResponse,
    EdgeRecomputeRequest,
    EntityCanonicalResponse,
    EntityEdgeResponse,
    EntityMergeRequest,
    EntityMergeResult,
    EntityResolutionStats,
    GraphTraversalResult,
    LintReportResponse,
    SurpriseConnection,
    TripleBatchCreate,
    TripleCreate,
    TripleInvalidateRequest,
    TripleResponse,
)
from .kg_services import (
    cascade_recall_service,
    community_service,
    community_summary_service,
    graph_traversal_service,
    triple_service,
)
from .lint import (
    format_health_report_markdown,
    remediate_knowledge_conflicts,
    remediate_orphans,
    remediate_semantic,
    remediate_stale,
    run_health_check,
    run_lint,
)

router = APIRouter(prefix="/kg", tags=["memvault-kg"])


# ======================== Triples ========================


@router.post("/triples", response_model=TripleResponse, status_code=201)
async def create_triple(
    body: TripleCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await triple_service.create(db, space_id, body)
    await db.commit()
    await db.refresh(instance)

    # Index to Qdrant (content-driven; embedding is generated inside index_document).
    # Drift-fix: Triple.embedding column was removed during the pgvector → Qdrant
    # migration; route writes through Qdrant instead of the SA model.
    try:
        await qdrant_search.index_document(
            IndexDocument(
                service_id="memvault",
                entity_id=instance.id,
                entity_type="triple",
                space_id=space_id,
                content=f"{instance.subject} {instance.predicate} {instance.object}",
                created_at=instance.created_at,
                updated_at=instance.updated_at,
                metadata={
                    "subject": instance.subject,
                    "predicate": instance.predicate,
                    "object": instance.object,
                },
            )
        )
    except Exception as e:  # best-effort — never block the write
        logger.warning("Qdrant index failed for triple %s: %s", instance.id, e)

    return triple_service.to_response(instance)


@router.post("/triples/batch", status_code=201)
async def batch_ingest_triples(
    body: TripleBatchCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    created = await triple_service.batch_ingest(db, space_id, body)
    await db.commit()
    return {"ingested": len(created), "session_id": body.session_id}


@router.get("/triples", response_model=PaginatedResponse[TripleResponse])
async def list_triples(
    space_id: str = Query("default"),
    predicate: str | None = Query(None),
    subject: str | None = Query(None),
    include_invalid: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    if predicate:
        results = await triple_service.search_by_predicate(
            db,
            space_id,
            predicate,
            subject=subject,
            include_invalid=include_invalid,
        )
        return PaginatedResponse(
            items=results,
            total=len(results),
            page=page,
            page_size=page_size,
        )
    pagination = PaginationParams(page=page, page_size=page_size)
    return await triple_service.list(db, space_id, pagination)


@router.get("/triples/search", response_model=list[TripleResponse])
async def search_triples(
    q: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(10, ge=1, le=100),
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    query_embedding = await get_embedding(q)
    if query_embedding:
        return await triple_service.semantic_search(db, space_id, query_embedding, top_k=top_k)
    # Text fallback when Ollama is unavailable
    return await triple_service.search_by_predicate(db, space_id, q, limit=top_k)


@router.delete("/triples/{triple_id}", status_code=204)
async def delete_triple(
    triple_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    await triple_service.delete_by_id(db, triple_id)
    await db.commit()
    return None


@router.put("/triples/{triple_id}", response_model=TripleResponse)
async def update_triple(
    triple_id: str,
    body: TripleCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await triple_service.update_by_id(db, triple_id, body)
    await db.commit()
    await db.refresh(instance)
    return triple_service.to_response(instance)


@router.put("/triples/{triple_id}/invalidate", response_model=TripleResponse)
async def invalidate_triple(
    triple_id: str,
    body: TripleInvalidateRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Mark a triple as invalid (soft temporal invalidation)."""
    instance = await triple_service.invalidate(
        db,
        triple_id,
        reason=body.reason,
        replacement_id=body.replacement_triple_id,
    )
    await db.commit()
    await db.refresh(instance)
    return triple_service.to_response(instance)


# ======================== Communities ========================


@router.get("/communities", response_model=list[CommunityResponse])
async def list_communities(
    space_id: str = Query("default"),
    resolution_level: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    return await community_service.list_communities(db, space_id, resolution_level=resolution_level)


@router.get("/communities/{community_id}", response_model=CommunityDetail)
async def get_community(
    community_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    detail = await community_service.get_community_detail(db, community_id)
    if not detail:
        raise NotFoundError("Community not found", code="memvault.community_not_found")
    return detail


@router.post("/communities/regenerate", status_code=200)
async def regenerate_communities(
    body: CommunityRegenerateRequest,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Accept community data from community_pipeline.py and save atomically.

    Converts pipeline format (triples with id) to service format (members with triple_id).
    """
    communities_for_service = []
    generation_batch = body.generated_at

    for c in body.communities:
        triple_ids = []
        for t in c.get("triples", []):
            triple_id = t.get("id", "")
            if triple_id:
                triple_ids.append(triple_id)

        communities_for_service.append(
            {
                "name": c.get("name", ""),
                "resolution_level": c.get("resolution_level", 0),
                "size": c.get("size", 0),
                "entity_ids": c.get("entity_ids"),
                "top_entities": c.get("top_entities"),
                "top_predicates": c.get("top_predicates"),
                "summary": c.get("summary"),
                "parent_community_id": c.get("parent_community_id"),
                "modularity_score": c.get("modularity_score"),
                "generation_batch": generation_batch,
                "triple_ids": triple_ids,
            }
        )

    saved = await community_service.save_communities(db, space_id, communities_for_service)
    await db.commit()
    return {"saved": saved, "generation_batch": generation_batch}


@router.post("/communities/{community_id}/description", status_code=200)
async def update_community_description(
    community_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Update a community's description_zh field."""
    from sqlalchemy import select

    from .kg_models import Community

    result = await db.execute(select(Community).where(Community.id == community_id))
    community = result.scalar_one_or_none()
    if not community:
        raise NotFoundError("Community not found", code="memvault.community_not_found")
    community.description_zh = body.get("description_zh", "")
    await db.commit()
    return {"id": community_id, "description_zh": community.description_zh}


# ======================== Community Summaries ========================


@router.get("/summaries", response_model=list[CommunitySummaryResponse])
async def list_summaries(
    space_id: str = Query("default"),
    resolution_level: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    return await community_summary_service.list_summaries(
        db, space_id, resolution_level=resolution_level
    )


@router.post("/summaries/regenerate", status_code=200)
async def regenerate_summaries(
    body: CommunitySummaryRegenerateRequest,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Accept community summary data from community_summary_pipeline.py and save atomically."""
    saved = await community_summary_service.save_summaries(db, space_id, body.summaries)
    await db.commit()
    return {"saved": saved, "generated_at": body.generated_at}


# ======================== Cascade Recall ========================


@router.get("/recall", response_model=CascadeRecallResult)
async def cascade_recall(
    q: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(5, ge=1, le=20),
    space_id: str = Query("default"),
    skip_routing: bool = Query(False, description="Bypass query router, search all layers"),
    evaluate: str = Query("default", pattern="^(default|deep|rlm|none)$"),
    db: AsyncSession = Depends(get_db),
):
    return await cascade_recall_service.recall(
        db,
        space_id,
        q,
        top_k=top_k,
        skip_routing=skip_routing,
        evaluate=evaluate,
    )


# ======================== Embedding Backfill ========================


# ======================== Entity Resolution ========================


@router.get("/entities", response_model=PaginatedResponse[EntityCanonicalResponse])
async def list_entities(
    space_id: str = Query("default"),
    entity_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List canonical entities with optional type filter."""
    from sqlalchemy import func, select

    from .kg_models import EntityCanonical

    q = select(EntityCanonical).where(
        EntityCanonical.space_id == space_id,
        EntityCanonical.deleted_at.is_(None),
    )
    if entity_type:
        q = q.where(EntityCanonical.entity_type == entity_type)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = q.order_by(EntityCanonical.canonical_name).offset((page - 1) * page_size).limit(page_size)
    entities = (await db.execute(q)).scalars().all()

    return PaginatedResponse(
        items=[entity_resolution_service.to_response(e) for e in entities],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/entities/stats", response_model=EntityResolutionStats)
async def entity_stats(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Get entity resolution statistics."""
    return await entity_resolution_service.get_stats(db, space_id)


@router.get(
    "/entities/merge-candidates",
    response_model=list[dict],
)
async def entity_merge_candidates(
    space_id: str = Query("default"),
    threshold: float = Query(0.92, ge=0.5, le=1.0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Find entities that are candidates for merging (high embedding similarity)."""
    candidates = await entity_resolution_service.find_merge_candidates(
        db,
        space_id,
        threshold=threshold,
        limit=limit,
    )
    return [
        {"primary": a.model_dump(), "secondary": b.model_dump(), "similarity": sim}
        for a, b, sim in candidates
    ]


@router.post("/entities/merge", response_model=EntityMergeResult)
async def merge_entities(
    body: EntityMergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Merge secondary entity into primary."""
    result = await entity_resolution_service.merge_entities(
        db,
        body.primary_id,
        body.secondary_id,
    )
    await db.commit()
    return result


@router.post("/entities/backfill", status_code=200)
async def backfill_entity_resolution(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Backfill entity resolution for triples missing canonical IDs."""
    from sqlalchemy import select

    from .kg_models import Triple

    q = select(Triple).where(
        Triple.space_id == space_id,
        Triple.deleted_at.is_(None),
        Triple.canonical_subject_id.is_(None),
    )
    triples = list((await db.execute(q)).scalars().all())
    resolved = await entity_resolution_service.batch_resolve_triples(db, space_id, triples)
    await db.commit()
    return {"total_unresolved": len(triples), "resolved": resolved}


@router.post("/entities/auto-merge", status_code=200)
async def auto_merge_entities(
    space_id: str = Query("default"),
    threshold: float = Query(0.95, ge=0.85, le=1.0),
    max_merges: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Auto-merge entity pairs above similarity threshold."""
    results = await entity_resolution_service.auto_merge(
        db, space_id, threshold=threshold, max_merges=max_merges
    )
    await db.commit()
    return {
        "merged": len(results),
        "details": [
            {
                "canonical_name": r.canonical_name,
                "aliases": r.aliases,
                "triples_updated": r.triples_updated,
            }
            for r in results
        ],
    }


# ======================== Attitudes ========================


@router.get("/attitudes")
async def list_attitudes(
    space_id: str = Query("default"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """List blocks of `block_type='attitude'` for the KAS chart.

    The frontend's "KAS 能力圖譜" panel polls this endpoint on every page
    load. v1.0.0 / v1.0.1 didn't expose it, so the UI rendered with five
    `console.error` entries on a fresh install (cosmetic — the chart
    itself fell back to attitude=0 gracefully).

    This is a thin pass-through over `MemoryBlock` filtered by
    block_type — same shape as the standard `/blocks` listing so the
    frontend's existing pagination renderer works without changes.
    The richer "Attitude system" (with risk/decision_style/communication
    sub-axes from src.memvault.README) is intentionally out of scope
    for v1.0.x and lands in v1.1.
    """
    from sqlalchemy import func, select

    from .models import MemoryBlock

    base_q = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.block_type == "attitude",
            MemoryBlock.deleted_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
    )
    total_q = select(func.count()).select_from(base_q.subquery())
    total = (await db.execute(total_q)).scalar_one()
    items_q = base_q.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(items_q)).scalars().all()
    return {
        "items": [
            {
                "id": b.id,
                "content": b.content,
                "tags": list(b.tags or []),
                "confidence": b.confidence,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "updated_at": b.updated_at.isoformat() if b.updated_at else None,
            }
            for b in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ======================== Graph Traversal ========================


@router.get("/traverse", response_model=GraphTraversalResult)
async def graph_traverse(
    entity: str = Query(..., min_length=1, max_length=500),
    space_id: str = Query("default"),
    max_depth: int = Query(2, ge=1, le=4),
    direction: str = Query("both", pattern="^(outgoing|incoming|both)$"),
    predicates: str | None = Query(None, description="Comma-separated predicate filter"),
    max_results: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Multi-hop graph traversal from a seed entity using recursive CTE."""
    entity = normalize_entity_text(entity)
    predicate_filter = (
        [p.strip() for p in predicates.split(",") if p.strip()] if predicates else None
    )
    return await graph_traversal_service.traverse(
        db,
        space_id,
        entity,
        max_depth=max_depth,
        direction=direction,
        predicate_filter=predicate_filter,
        max_results=max_results,
    )


# ======================== Embedding Backfill ========================


@router.post("/embeddings/backfill", status_code=200)
async def backfill_embeddings(
    space_id: str = Query("default"),
    batch_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Backfill missing Qdrant entries for triples.

    Drift-fix: pre-Qdrant migration this used `WHERE embedding IS NULL` on the
    SA model. Now diff-driven: scroll Qdrant for already-indexed entity_ids,
    LEFT-anti-join against Postgres triples, then batch-index via Qdrant.

    NOTE: depends on `qdrant_search.scroll_by_service` (see
    docs/embedding_drift_patches.md §5) — apply that helper to
    src/shared/qdrant_search.py before this endpoint will work.
    """
    from sqlalchemy import select

    from .kg_models import Triple

    # 1. Snapshot already-indexed entity_ids from Qdrant
    indexed_ids = await qdrant_search.scroll_by_service(
        service_id="memvault",
        entity_type="triple",
        space_id=space_id,
    )

    # 2. Fetch all Postgres triples for this space
    triple_q = (
        select(Triple)
        .where(Triple.space_id == space_id)
        .order_by(Triple.created_at)
    )
    result = await db.execute(triple_q)
    all_triples = list(result.scalars().all())

    # 3. Anti-join: keep only triples not yet in Qdrant
    missing = [t for t in all_triples if t.id not in indexed_ids]

    triple_updated = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        docs = [
            IndexDocument(
                service_id="memvault",
                entity_id=t.id,
                entity_type="triple",
                space_id=space_id,
                content=f"{t.subject} {t.predicate} {t.object}",
                created_at=t.created_at,
                updated_at=t.updated_at,
                metadata={
                    "subject": t.subject,
                    "predicate": t.predicate,
                    "object": t.object,
                },
            )
            for t in batch
        ]
        triple_updated += await qdrant_search.index_documents_batch(docs)

    return {
        "triples": {
            "total_missing": len(missing),
            "updated": triple_updated,
            "already_indexed": len(indexed_ids),
        },
    }


# ======================== Session Context (Gap 2: Block ↔ Triple Bridge) ========================


@router.get("/session-context")
async def get_session_context(
    source_session: str = Query(..., min_length=1, max_length=200),
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Return all blocks, triples, and entities for a given source_session.

    Bridges the two parallel outputs (blocks + triples) that share the
    same source_session, enabling unified session context retrieval.
    """
    from sqlalchemy import select

    from .kg_models import EntityCanonical, Triple
    from .models import MemoryBlock

    # Blocks for this session
    block_q = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.source_session == source_session,
            MemoryBlock.deleted_at.is_(None),
        )
        .order_by(MemoryBlock.created_at)
    )
    blocks = (await db.execute(block_q)).scalars().all()

    # Triples for this session
    triple_q = (
        select(Triple)
        .where(
            Triple.space_id == space_id,
            Triple.source_session == source_session,
            Triple.deleted_at.is_(None),
        )
        .order_by(Triple.created_at)
    )
    triples = (await db.execute(triple_q)).scalars().all()

    # Collect canonical entity IDs from triples
    entity_ids = set()
    for t in triples:
        if t.canonical_subject_id:
            entity_ids.add(t.canonical_subject_id)
        if t.canonical_object_id:
            entity_ids.add(t.canonical_object_id)

    entities = []
    if entity_ids:
        entity_q = select(EntityCanonical).where(
            EntityCanonical.id.in_(entity_ids),
            EntityCanonical.deleted_at.is_(None),
        )
        entities = (await db.execute(entity_q)).scalars().all()

    return {
        "source_session": source_session,
        "space_id": space_id,
        "blocks": [
            {
                "id": b.id,
                "content": b.content,
                "block_type": b.block_type,
                "tags": b.tags or [],
                "confidence": b.confidence or 0.0,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in blocks
        ],
        "triples": [
            {
                "id": t.id,
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "invalid_at": t.invalid_at.isoformat() if t.invalid_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in triples
        ],
        "entities": [
            {
                "id": e.id,
                "canonical_name": e.canonical_name,
                "aliases": e.aliases or [],
                "entity_type": e.entity_type or "concept",
                "merge_count": e.merge_count or 1,
            }
            for e in entities
        ],
        "summary": {
            "total_blocks": len(blocks),
            "total_triples": len(triples),
            "total_entities": len(entities),
        },
    }


# =============== Intelligence Event Publish (Gap 3: Station → Core) ===============


@router.post("/intelligence/ingest", status_code=200)
async def ingest_intelligence_digest(
    space_id: str = Query("default"),
    digest_type: str = Query("weekly", pattern="^(daily|weekly)$"),
    period: str = Query("", description="Period label, e.g., '2026-W11'"),
    content: str = Query(..., min_length=10),
    db: AsyncSession = Depends(get_db),
):
    """HTTP bridge for session-intelligence station to push digests into Core.

    Publishes a SessionIntelligenceEvents.DIGEST_COMPLETED event, which the
    memvault event handler stores as a knowledge block + auto-extracts KG triples.
    """
    from src.events_stub.types import SessionIntelligenceEvents

    await event_bus.publish(
        Event(
            type=SessionIntelligenceEvents.DIGEST_COMPLETED,
            data={
                "space_id": space_id,
                "digest_type": digest_type,
                "period": period,
                "content": content,
                "tags": ["intelligence", "digest", digest_type],
            },
            source="session-intelligence",
        )
    )

    return {
        "status": "ingested",
        "digest_type": digest_type,
        "period": period,
        "space_id": space_id,
    }


# ======================== Interest Profile ========================


@router.post("/interest/generate", status_code=200)
async def generate_interest_snapshot(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Generate daily interest snapshot from query_journal.

    Called by synthesis_runner Step 3 or manually for testing.
    """
    result = await interest_profile_service.generate_daily_snapshot(db, space_id)
    await db.commit()
    return result


@router.get("/interest/attention", status_code=200)
async def get_attention_profile(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Get the latest attention profile — which entities are active/fading/historical.

    Used by PersonalizedQueryRouter (cached via Redis).
    """
    return await interest_profile_service.get_attention_profile(db, space_id)


@router.get("/interest/gaps", status_code=200)
async def get_knowledge_gaps(
    space_id: str = Query("default"),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get recurring knowledge gaps — queries that repeatedly fail.

    Returns queries with INCORRECT verdict that appeared 2+ times in the period.
    """
    return await interest_profile_service.get_knowledge_gaps(db, space_id, days=days, limit=limit)

# ======================== KG Lint ========================


@router.post("/lint", response_model=LintReportResponse)
async def lint_knowledge_graph(
    checks: str = Query("all", description="Comma-separated check names or all"),
    fix: bool = Query(False, description="Apply safe remediations"),
    dry_run: bool = Query(True, description="Preview-only when fix=True"),
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Knowledge graph health check — detect contradictions, stale triples, orphans, etc."""
    if fix and not dry_run:
        from src.auth_stub import has_permission

        if not has_permission(_user.get("role", "guest"), "memvault.write"):
            raise ForbiddenError(
                "Permission denied: memvault.write required for fix with dry_run=False",
                code="memvault.forbidden",
            )
    check_list = None if checks == "all" else [c.strip() for c in checks.split(",")]
    report = await run_lint(db, space_id, checks=check_list, use_pipeline=True)

    remediations = 0
    if fix:
        stale_findings = [f for f in report.findings if f.check == "stale"]
        orphan_findings = [f for f in report.findings if f.check == "orphan_entities"]
        remediations += await remediate_stale(db, stale_findings, dry_run=dry_run)
        remediations += await remediate_orphans(db, orphan_findings, dry_run=dry_run)
        semantic_findings = [f for f in report.findings if f.check == "semantic_contradictions"]
        remediations += await remediate_semantic(db, semantic_findings, dry_run=dry_run)
        kc_findings = [f for f in report.findings if f.check == "knowledge_conflicts"]
        remediations += await remediate_knowledge_conflicts(db, kc_findings, dry_run=dry_run)
        from .lint import remediate_attitude_conflicts

        att_findings = [
            f
            for f in report.findings
            if f.check in ("attitude_semantic_contradictions", "attitude_temporal_staleness")
        ]
        remediations += await remediate_attitude_conflicts(db, att_findings, dry_run=dry_run)

    return LintReportResponse(
        space_id=report.space_id,
        checks_run=report.checks_run,
        findings=[
            {
                "check": f.check,
                "severity": f.severity,
                "entity_id": f.entity_id,
                "entity_type": f.entity_type,
                "message": f.message,
                "suggested_action": f.suggested_action,
                "metadata": f.metadata,
            }
            for f in report.findings
        ],
        summary=report.summary,
        run_duration_ms=report.run_duration_ms,
        run_at=report.run_at.isoformat(),
        remediations_applied=remediations,
    )


# ======================== Knowledge Lint v2 — Wiki-Lint Health Report ========================


@router.get("/lint/report", response_model=LintReportResponse)
async def lint_health_report(
    checks: str = Query(
        "all",
        description=(
            "Comma-separated subset of: orphan_blocks,dead_triples,stale_claims,"
            "missing_entities,missing_cross_refs,metadata_gaps,empty_content,"
            "stale_index_entries,stable_id_validity,semantic_tiling_dedup. "
            "Use 'all' to run every wiki-lint check."
        ),
    ),
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Run the 10 wiki-lint inspired health checks (report only — no remediation)."""
    only = None if checks == "all" else [c.strip() for c in checks.split(",") if c.strip()]
    report = await run_health_check(db, space_id, only=only)
    return LintReportResponse(
        space_id=report.space_id,
        checks_run=report.checks_run,
        findings=[
            {
                "check": f.check,
                "severity": f.severity,
                "entity_id": f.entity_id,
                "entity_type": f.entity_type,
                "message": f.message,
                "suggested_action": f.suggested_action,
                "metadata": f.metadata,
            }
            for f in report.findings
        ],
        summary=report.summary,
        run_duration_ms=report.run_duration_ms,
        run_at=report.run_at.isoformat(),
        remediations_applied=0,
    )


@router.get("/lint/report.md", response_class=PlainTextResponse)
async def lint_health_report_markdown(
    checks: str = Query("all"),
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Same as `/kg/lint/report` but returns wiki-lint markdown for humans/CLI."""
    only = None if checks == "all" else [c.strip() for c in checks.split(",") if c.strip()]
    report = await run_health_check(db, space_id, only=only)
    return format_health_report_markdown(report)


# ======================== Entity Edges (Multi-Signal) ========================


@router.get("/entity-edges", response_model=list[EntityEdgeResponse])
async def list_entity_edges(
    space_id: str = Query(default="default"),
    min_weight: float = Query(default=0.1),
    limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """List entity edges with composite_weight >= min_weight."""
    from sqlalchemy import select

    from .kg_models import EntityCanonical, EntityEdge

    ea = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("ea")
    eb = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("eb")

    stmt = (
        select(
            EntityEdge,
            ea.c.canonical_name.label("name_a"),
            eb.c.canonical_name.label("name_b"),
        )
        .join(ea, EntityEdge.entity_a_id == ea.c.id)
        .join(eb, EntityEdge.entity_b_id == eb.c.id)
        .where(
            EntityEdge.space_id == space_id,
            EntityEdge.deleted_at.is_(None),
            EntityEdge.composite_weight >= min_weight,
        )
        .order_by(EntityEdge.composite_weight.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)

    return [
        EntityEdgeResponse(
            id=row[0].id,
            space_id=row[0].space_id,
            created_at=row[0].created_at,
            updated_at=row[0].updated_at,
            entity_a_id=row[0].entity_a_id,
            entity_b_id=row[0].entity_b_id,
            entity_a_name=row.name_a or "",
            entity_b_name=row.name_b or "",
            cooccurrence_count=row[0].cooccurrence_count,
            session_overlap=row[0].session_overlap,
            adamic_adar=row[0].adamic_adar,
            type_affinity=row[0].type_affinity,
            semantic_similarity=row[0].semantic_similarity,
            composite_weight=row[0].composite_weight,
            last_computed_at=row[0].last_computed_at,
        )
        for row in result
    ]


@router.post("/entity-edges/recompute", status_code=202)
async def recompute_edge_weights(
    space_id: str = Query(default="default"),
    body: EdgeRecomputeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Trigger full edge weight recomputation via the Edge Pipeline."""

    from .pipeline_config import MemvaultPipelineConfig
    from .pipelines.edge_pipeline import build_edge_pipeline

    config = MemvaultPipelineConfig.from_env()
    pipeline = build_edge_pipeline(config)

    ctx = {"db": db, "space_id": space_id}
    ctx = await pipeline.execute(ctx)

    return {
        "edges_upserted": ctx.get("edges_upserted", 0),
        "meta": {
            "stages_applied": ctx.get("_pipeline_meta", {}).stages_applied
            if hasattr(ctx.get("_pipeline_meta"), "stages_applied")
            else [],
            "stage_timings": ctx.get("_pipeline_meta", {}).stage_timings
            if hasattr(ctx.get("_pipeline_meta"), "stage_timings")
            else {},
        },
    }


# ======================== Surprise Connections ========================


@router.get("/entity-edges/surprises", response_model=list[SurpriseConnection])
async def get_surprise_connections(
    space_id: str = Query(default="default"),
    strategy: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Discover unexpected knowledge connections via multi-signal analysis."""
    from .pipeline_config import MemvaultPipelineConfig
    from .pipelines.surprise_pipeline import build_surprise_pipeline

    config = MemvaultPipelineConfig.from_env()
    config.surprise_limit = limit
    pipeline = build_surprise_pipeline(config)

    ctx = {"db": db, "space_id": space_id}
    ctx = await pipeline.execute(ctx)

    surprises = ctx.get("surprises", [])

    # Filter by strategy if specified
    if strategy:
        surprises = [s for s in surprises if s.get("strategy") == strategy]

    return [SurpriseConnection(**s) for s in surprises]
