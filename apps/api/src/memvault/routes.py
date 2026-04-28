"""Memvault routes — REST API endpoints.

Prefix: /api/memvault (mounted in main.py)
"""

import asyncio
import logging
import math
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.deps import get_db, require_permission
from src.shared.errors import BadRequestError, NotFoundError
from src.shared.schemas import PaginatedResponse, PaginationParams
from text_ops.merge import merge_content
from text_ops.noise import check_noise

from .dedup import DedupDecision, check_duplicate
from .embedding import get_embedding
from .injection_guard import is_unsafe_for_injection, sanitize_for_injection
from .query_runtime import build_injection_payload, build_inspect_payload, run_memory_query
from .recall_text import build_recall_text
from .schemas import (
    EnhancedSearchResult,
    FrontierNodeResponse,
    FrontierTopResponse,
    KnowledgeDomainCreate,
    KnowledgeDomainResponse,
    KnowledgeDomainUpdate,
    MemoryBlockCreate,
    MemoryBlockResponse,
    MemoryBlockUpdate,
    MemoryInjectResponse,
    MemoryInspectResponse,
    MemoryQueryRequest,
    MemoryQueryResponse,
    ProfileScoreResponse,
    ProfileScoreUpdate,
    SearchFeedbackCreate,
    SearchMetadata,
    SemanticSearchParams,  # noqa: F401 — available for future use
    SessionSummary,
    TagResponse,
)
from .services import (
    knowledge_domain_service,
    memory_block_service,
    profile_score_service,
    should_search,
    tag_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memvault"])


# ======================== Memory Blocks ========================


@router.get("/blocks", response_model=PaginatedResponse[MemoryBlockResponse])
async def list_blocks(
    space_id: str = Query("default"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tag: str | None = Query(None, description="Single tag filter"),
    tags: str | None = Query(None, description="Comma-separated tag filter"),
    block_type: str | None = Query(None, description="Block type filter"),
    date_from: datetime | None = Query(None, description="Filter: created_at >= date_from"),
    date_to: datetime | None = Query(None, description="Filter: created_at <= date_to"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    pagination = PaginationParams(page=page, page_size=page_size)
    # Support both singular 'tag' and plural 'tags' params
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif tag:
        tag_list = [tag]

    if tag_list:
        return await memory_block_service.list_by_tags(
            db,
            space_id,
            tag_list,
            pagination,
            date_from=date_from,
            date_to=date_to,
        )
    if block_type:
        return await memory_block_service.list_by_type(
            db,
            space_id,
            block_type,
            pagination,
            date_from=date_from,
            date_to=date_to,
        )
    return await memory_block_service.list(
        db,
        space_id,
        pagination,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/blocks/{block_id}", response_model=MemoryBlockResponse)
async def get_block(
    block_id: str,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    instance = await memory_block_service.get_in_space(db, block_id, space_id)
    if not instance:
        raise NotFoundError("Block not found", code="memvault.block_not_found")
    return memory_block_service.to_response(instance)


@router.post("/blocks", response_model=MemoryBlockResponse, status_code=201)
async def create_block(
    body: MemoryBlockCreate,
    space_id: str = Query("default"),
    skip_dedup: bool = Query(False, description="Skip dedup check"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    # --- Write gates: Noise → Injection Guard → Dedup (sequential) ---
    # Noise and Injection Guard MUST run before Dedup to prevent noisy/unsafe
    # content from polluting existing blocks via MERGE decisions.
    is_quarantined = False

    # Gate 1: Noise check — noisy content skips dedup entirely
    noise_verdict = check_noise(body.content or "")
    if noise_verdict.is_noise:
        is_quarantined = True
        logger.info("Write gate: noise quarantine (%s), skipping dedup", noise_verdict.reason)

    # Gate 2: Injection guard — unsafe content skips dedup entirely
    if not is_quarantined:
        unsafe, inject_reason = is_unsafe_for_injection(body.content or "")
        if unsafe:
            is_quarantined = True
            logger.warning("Write gate: injection quarantine (%s), skipping dedup", inject_reason)

    # Gate 3: Dedup check — only clean content reaches similarity comparison
    try:
        embedding = await get_embedding(body.content, task_type="search_document")
    except Exception:
        logger.warning("Embedding failed for dedup check, skipping", exc_info=True)
        embedding = None

    superseded_block_id = None
    if embedding and not skip_dedup and not is_quarantined:
        dedup_result = await check_duplicate(
            db, space_id, body.content, embedding, block_type=body.block_type
        )

        if dedup_result.decision == DedupDecision.SKIP:
            # Near-identical block exists — return existing
            logger.info(
                "Dedup SKIP: %s (existing=%s)",
                dedup_result.reason,
                dedup_result.existing_block_id,
            )
            existing = await memory_block_service.get(db, dedup_result.existing_block_id)
            if existing:
                return memory_block_service.to_response(existing)

        if dedup_result.decision == DedupDecision.MERGE:
            # Merge new content into existing block
            logger.info(
                "Dedup MERGE: %s (existing=%s)",
                dedup_result.reason,
                dedup_result.existing_block_id,
            )
            existing = await memory_block_service.get(db, dedup_result.existing_block_id)
            if existing:
                merged = merge_content(existing.content, body.content)
                from .schemas import MemoryBlockUpdate

                await memory_block_service.update(
                    db,
                    dedup_result.existing_block_id,
                    MemoryBlockUpdate(content=merged),
                )
                # Re-embed with merged content (best-effort)
                try:
                    merged_emb = await get_embedding(merged, task_type="search_document")
                    if merged_emb:
                        await memory_block_service.update_embedding(
                            db, dedup_result.existing_block_id, merged_emb
                        )
                except Exception:
                    logger.warning("Embedding failed for merge, skipping", exc_info=True)
                await db.commit()
                await db.refresh(existing)
                return memory_block_service.to_response(existing)

        if dedup_result.decision == DedupDecision.SUPERSEDE:
            # Invalidate old block and fall through to create the new one
            logger.info(
                "Dedup SUPERSEDE: %s (existing=%s)",
                dedup_result.reason,
                dedup_result.existing_block_id,
            )
            if dedup_result.existing_block_id:
                superseded_block_id = dedup_result.existing_block_id
                await memory_block_service.invalidate_block(
                    db,
                    block_id=superseded_block_id,
                    superseded_by_id="__pending__",
                    reason="superseded",
                )

    # Normal creation path
    instance = await memory_block_service.create(db, space_id, body)
    # Override created_at if caller provided actual event time (e.g. session timestamp)
    if body.created_at:
        instance.created_at = body.created_at
    try:
        if embedding:
            await memory_block_service.update_embedding(db, instance.id, embedding)
    except Exception:
        logger.warning("Failed to store embedding for block %s", instance.id, exc_info=True)

    # Backfill superseded_by with the actual new block ID
    if superseded_block_id:
        await memory_block_service.invalidate_block(
            db,
            block_id=superseded_block_id,
            superseded_by_id=str(instance.id),
            reason="superseded",
        )

    await db.commit()
    await db.refresh(instance)
    return memory_block_service.to_response(instance)


@router.put("/blocks/{block_id}", response_model=MemoryBlockResponse)
async def update_block(
    block_id: str,
    body: MemoryBlockUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await memory_block_service.update(db, block_id, body)
    if not instance:
        raise NotFoundError("Block not found", code="memvault.block_not_found")
    await db.commit()
    await db.refresh(instance)
    return memory_block_service.to_response(instance)


@router.delete("/blocks/{block_id}", status_code=204)
async def delete_block(
    block_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    deleted = await memory_block_service.delete(db, block_id)
    if not deleted:
        raise NotFoundError("Block not found", code="memvault.block_not_found")
    await db.commit()


# ======================== Sessions ========================


@router.get("/sessions", response_model=PaginatedResponse[SessionSummary])
async def list_sessions(
    space_id: str = Query("default"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Aggregate blocks by source_session — block_count, first/last timestamps, block types."""
    from sqlalchemy.dialects.postgresql import array_agg

    from .models import MemoryBlock

    base = (
        select(
            MemoryBlock.source_session,
            func.count().label("block_count"),
            func.min(MemoryBlock.created_at).label("first_at"),
            func.max(MemoryBlock.created_at).label("last_at"),
            array_agg(func.distinct(MemoryBlock.block_type)).label("block_types"),
        )
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.source_session.isnot(None),
            MemoryBlock.deleted_at.is_(None),
        )
        .group_by(MemoryBlock.source_session)
    )

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    rows = (
        await db.execute(
            base.order_by(func.max(MemoryBlock.created_at).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return PaginatedResponse[SessionSummary](
        items=[
            SessionSummary(
                source_session=r.source_session,
                block_count=r.block_count,
                first_at=r.first_at,
                last_at=r.last_at,
                block_types=r.block_types or [],
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# ======================== Semantic Search ========================


_ROUTING_MIN_RESULTS = 2  # Minimum results before routing fallback triggers


@router.get("/search", response_model=EnhancedSearchResult)
async def search(
    q: str = Query(..., min_length=1, max_length=2000),
    top_k: int = Query(10, ge=1, le=100),
    space_id: str = Query("default"),
    include_metadata: bool = Query(False, description="Include scoring metadata"),
    skip_adaptive: bool = Query(False, description="Force search even if adaptive says skip"),
    skip_routing: bool = Query(False, description="Skip tag-based pre-filter for A/B testing"),
    scope: str | None = Query(
        None,
        description="Scope filter: global, session:{id}, user:{id}, type:{type}. Comma-separated.",
    ),
    date_from: datetime | None = Query(None, description="Filter: created_at >= date_from"),
    date_to: datetime | None = Query(None, description="Filter: created_at <= date_to"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    # Phase B2: Adaptive Retrieval
    if not skip_adaptive:
        do_search, reason = should_search(q)
        if not do_search:
            meta = SearchMetadata(
                adaptive_skipped=True,
                adaptive_reason=reason,
                vector_used=False,
                scoring_applied=False,
                input_count=0,
                output_count=0,
                scope=scope,
            )
            return EnhancedSearchResult(
                results=[],
                metadata=meta if include_metadata else None,
            )

    # HyDE: Expand query for better retrieval (short/vague queries → hypothetical memory)
    expanded = None
    try:
        from .query_expander import expand_query

        expanded = await expand_query(q)
        embed_text = expanded.expanded_text
        logger.debug(
            "query_expander: %s → %r (%s)", q[:30], embed_text[:50], expanded.expansion_used
        )
    except Exception:
        embed_text = q  # fallback to original query

    # Extract inferred domain tags for pre-filtering (safe fallback: empty = full search)
    inferred_tags = expanded.inferred_tags if expanded else []
    routing_tags = inferred_tags if (inferred_tags and not skip_routing) else None

    # Temporal: use resolved dates from query if caller didn't pass explicit ones
    if expanded and not date_from and expanded.temporal_from:
        try:
            date_from = datetime.fromisoformat(expanded.temporal_from).replace(
                hour=0,
                minute=0,
                second=0,
                tzinfo=UTC,
            )
        except ValueError:
            pass
    if expanded and not date_to and expanded.temporal_to:
        try:
            date_to = datetime.fromisoformat(expanded.temporal_to).replace(
                hour=23,
                minute=59,
                second=59,
                tzinfo=UTC,
            )
        except ValueError:
            pass

    try:
        query_embedding = await get_embedding(embed_text, task_type="search_query")
    except Exception:
        logger.warning("Embedding failed for search query, falling back to text", exc_info=True)
        query_embedding = None
    if query_embedding is None:
        # Fallback: ILIKE text search when Ollama is unavailable
        results = await memory_block_service.text_search(
            db,
            space_id,
            q,
            top_k,
            scope=scope,
            date_from=date_from,
            date_to=date_to,
        )
        meta = SearchMetadata(
            vector_used=False,
            keyword_used=True,
            scoring_applied=False,
            input_count=len(results),
            output_count=len(results),
            scope=scope,
        )
        return EnhancedSearchResult(
            results=results,
            metadata=meta if include_metadata else None,
        )

    # Qdrant hybrid search (primary backend)
    qdrant_result = await memory_block_service.qdrant_search(
        db,
        space_id,
        q,
        query_embedding,
        top_k=top_k,
        scope=scope,
        date_from=date_from,
        date_to=date_to,
        tags=routing_tags,
    )

    # Routing fallback: if tag pre-filter produced sparse results, retry without tags
    if qdrant_result is not None and routing_tags and len(qdrant_result[0]) < _ROUTING_MIN_RESULTS:
        logger.debug(
            "routing fallback: tags=%s produced %d results (< %d), retrying without tags",
            routing_tags,
            len(qdrant_result[0]),
            _ROUTING_MIN_RESULTS,
        )
        qdrant_result = await memory_block_service.qdrant_search(
            db,
            space_id,
            q,
            query_embedding,
            top_k=top_k,
            scope=scope,
            date_from=date_from,
            date_to=date_to,
        )

    if qdrant_result is not None:
        results, meta = qdrant_result
        # H1: Sanitize ALL search paths (not just legacy fallback)
        for r in results:
            unsafe, reason = is_unsafe_for_injection(r.block.content)
            if unsafe:
                r.block.content = sanitize_for_injection(r.block.content)
                meta.injection_sanitized = (meta.injection_sanitized or 0) + 1
        meta.routing_tags = inferred_tags if inferred_tags else None

        # Temporal fallback: semantic search returned 0 but temporal dates are active
        # → list blocks in date range (temporal queries like "上週做了什麼" need listing, not matching)
        if not results and expanded and expanded.temporal_from:
            from .schemas import SemanticSearchResult

            blocks_page = await memory_block_service.list(
                db,
                space_id,
                PaginationParams(page=1, page_size=top_k),
                date_from=date_from,
                date_to=date_to,
            )
            results = [
                SemanticSearchResult(
                    block=b,
                    score=1.0,
                )
                for b in blocks_page.items
            ]
            meta.temporal_fallback = True

        return EnhancedSearchResult(
            results=results,
            metadata=meta if include_metadata else None,
        )

    # LEGACY FALLBACK: Qdrant unavailable — using pgvector semantic_search
    logger.warning("Qdrant unavailable — using legacy pgvector path for search")
    hyde_keywords = getattr(expanded, "keywords", None) if expanded else None

    results, meta = await memory_block_service.semantic_search(
        db,
        space_id,
        query_embedding,
        top_k=top_k,
        query=q,
        scope=scope,
        date_from=date_from,
        date_to=date_to,
        keywords=hyde_keywords,
    )
    meta.routing_tags = inferred_tags if inferred_tags else None

    # G2: Sanitize results before returning (prevents injection via stored memories)
    for r in results:
        unsafe, reason = is_unsafe_for_injection(r.block.content)
        if unsafe:
            r.block.content = sanitize_for_injection(r.block.content)
            meta.injection_sanitized = (meta.injection_sanitized or 0) + 1

    return EnhancedSearchResult(
        results=results,
        metadata=meta if include_metadata else None,
    )


@router.post("/query", response_model=MemoryQueryResponse)
async def query_memory(
    body: MemoryQueryRequest,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    return await run_memory_query(db, space_id, body)


@router.post("/inject", response_model=MemoryInjectResponse)
async def inject_memory(
    body: MemoryQueryRequest,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    response = await run_memory_query(db, space_id, body)
    return build_injection_payload(response)


@router.post("/inspect", response_model=MemoryInspectResponse)
async def inspect_memory(
    body: MemoryQueryRequest,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    request = body.model_copy(update={"thinking_mode": "slow", "consumer": "human"})
    response = await run_memory_query(db, space_id, request)
    return build_inspect_payload(response)


# ======================== Recall Text (hook entry) ========================


class RecallTextRequest(BaseModel):
    prompt: str
    session_id: str | None = None
    cwd: str | None = None


@router.post("/recall/text", response_class=PlainTextResponse)
async def recall_text(body: RecallTextRequest = Body(...)) -> PlainTextResponse:
    """Build the markdown recall block for a Claude Code prompt.

    Replaces the `recall.py` subprocess that the UserPromptSubmit hook used
    to fork. Body matches the original stdin JSON; response is plain text
    (may be empty).
    """
    text = await asyncio.to_thread(
        build_recall_text,
        body.prompt,
        body.session_id or "",
        body.cwd or "",
    )
    return PlainTextResponse(content=text or "")


@router.get("/prefetch/metrics")
async def prefetch_metrics(
    space_id: str = Query("default"),
    _user: dict = require_permission("memvault.read"),
):
    """Dashboard endpoint for Slow Thinker prefetch metrics.

    Note: space_id is taken from query param but validated against user's space
    in production. Currently solo-user, so memvault.read is sufficient.
    """
    from src.shared.prefetch import SpeculativePrefetchCache

    # TODO: Add ABAC owner-only check when multi-tenant
    cache = SpeculativePrefetchCache(module="memvault")
    metrics = await cache.get_metrics(space_id)
    return {
        "space_id": space_id,
        "query_count": metrics.query_count,
        "prefetch_count": metrics.prefetch_count,
        "hit_count": metrics.hit_count,
        "miss_count": metrics.miss_count,
        "waste_count": metrics.waste_count,
        "skip_count": metrics.skip_count,
        "hit_rate": round(metrics.hit_rate, 4),
        "waste_rate": round(metrics.waste_rate, 4),
        "avg_latency_saved_ms": round(metrics.avg_latency_saved_ms, 1),
        "compute_cost_ms": round(metrics.compute_cost_ms, 1),
    }


# ======================== Tags ========================


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    return await tag_service.list_tags(db, space_id)


@router.post("/tags/sync", status_code=200)
async def sync_tags(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    # No auth — internal pipeline endpoint (matches /communities/regenerate pattern)
    count = await tag_service.sync_tags(db, space_id)
    await db.commit()
    return {"synced": count}


# ======================== Knowledge Domains ========================


@router.get("/domains", response_model=PaginatedResponse[KnowledgeDomainResponse])
async def list_domains(
    space_id: str = Query("default"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    pagination = PaginationParams(page=page, page_size=page_size)
    return await knowledge_domain_service.list(db, space_id, pagination)


@router.post("/domains", response_model=KnowledgeDomainResponse, status_code=201)
async def create_domain(
    body: KnowledgeDomainCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await knowledge_domain_service.create(db, space_id, body)
    await db.commit()
    return knowledge_domain_service.to_response(instance)


@router.patch("/domains/{domain_id}", response_model=KnowledgeDomainResponse)
async def update_domain(
    domain_id: str,
    body: KnowledgeDomainUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await knowledge_domain_service.update(db, domain_id, body)
    if not instance:
        raise NotFoundError("Domain not found", code="memvault.domain_not_found")
    await db.commit()
    return knowledge_domain_service.to_response(instance)


# ======================== Profile Score ========================


@router.get("/profile", response_model=ProfileScoreResponse)
async def get_profile(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    profile = await profile_score_service.get_by_space(db, space_id)
    if not profile:
        # Return a default empty profile instead of 404
        return ProfileScoreResponse(
            id="",
            space_id=space_id,
            created_by=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            knowledge_score=0.0,
            attitude_score=0.0,
        )
    return profile


@router.put("/profile", response_model=ProfileScoreResponse)
async def upsert_profile(
    body: ProfileScoreUpdate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    result = await profile_score_service.upsert(db, space_id, body)
    await db.commit()
    return result


@router.post("/profile/recalculate", response_model=ProfileScoreResponse)
async def recalculate_profile(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Recalculate profile scores from actual KG data (post-KAS separation)."""
    from .kg_models import Community, CommunitySummary, Triple
    from .models import MemoryBlock

    # Knowledge score: based on triples + clusters + wisdom
    triple_count = (
        await db.execute(
            select(func.count()).select_from(Triple).where(Triple.space_id == space_id)
        )
    ).scalar() or 0
    cluster_count = (
        await db.execute(
            select(func.count()).select_from(Community).where(Community.space_id == space_id)
        )
    ).scalar() or 0
    wisdom_count = (
        await db.execute(
            select(func.count())
            .select_from(CommunitySummary)
            .where(CommunitySummary.space_id == space_id)
        )
    ).scalar() or 0

    # K score: log-scaled, 100 triples = ~50, 1000+ = ~80, + bonus for clusters/wisdom
    k_base = min(math.log10(max(triple_count, 1)) / math.log10(2000) * 70, 70)
    k_cluster_bonus = min(cluster_count * 2, 15)
    k_wisdom_bonus = min(wisdom_count * 2, 15)
    knowledge_score = round(min(k_base + k_cluster_bonus + k_wisdom_bonus, 100), 1)

    # Attitude score: based on attitude blocks (migrated from attitude_facts)
    att_count = (
        await db.execute(
            select(func.count())
            .select_from(MemoryBlock)
            .where(MemoryBlock.space_id == space_id, MemoryBlock.block_type == "attitude")
        )
    ).scalar() or 0
    a_base = min(math.log10(max(att_count, 1)) / math.log10(500) * 60, 60)
    attitude_score = round(min(a_base, 100), 1)

    # Upsert profile
    result = await profile_score_service.upsert(
        db,
        space_id,
        ProfileScoreUpdate(
            knowledge_score=knowledge_score,
            attitude_score=attitude_score,
        ),
    )
    await db.commit()
    return result


# ======================== Sync ========================


@router.get("/sync/stats")
async def sync_stats(
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
):
    """Return extraction stats based on DB data.

    Counts distinct source_sessions across blocks and triples to show
    how many sessions have been successfully ingested.
    """
    from .kg_models import Triple
    from .models import MemoryBlock

    await db.execute(
        select(func.count(func.distinct(MemoryBlock.source_session))).where(
            MemoryBlock.space_id == space_id, MemoryBlock.source_session.isnot(None)
        )
    )

    await db.execute(
        select(func.count(func.distinct(Triple.source_session))).where(
            Triple.space_id == space_id, Triple.source_session.isnot(None)
        )
    )

    # Union of unique sessions across both tables
    from sqlalchemy import union

    block_q = select(MemoryBlock.source_session).where(
        MemoryBlock.space_id == space_id, MemoryBlock.source_session.isnot(None)
    )
    triple_q = select(Triple.source_session).where(
        Triple.space_id == space_id, Triple.source_session.isnot(None)
    )
    combined = union(block_q, triple_q).subquery()
    total_synced = (await db.execute(select(func.count()).select_from(combined))).scalar() or 0

    return {
        "total": total_synced,
        "synced": total_synced,
        "failed": 0,
        "skipped": 0,
    }


@router.post("/sync/scan")
async def sync_scan():
    """Session extraction is handled automatically by the SessionEnd hook pipeline.

    This endpoint returns a stub result. Use extract-v2-async.sh hook for live extraction.
    """
    return {
        "total": 0,
        "synced": 0,
        "failed": 0,
        "skipped": 0,
        "already": 0,
        "log": "Session extraction is handled automatically by SessionEnd hook pipeline.",
    }


# ======================== Search Feedback ========================


@router.post("/feedback", status_code=201)
async def record_feedback(
    body: SearchFeedbackCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Record explicit relevance feedback for a search result (positive/negative)."""
    from .schemas import SearchFeedbackResponse
    from .services import search_feedback_service

    fb = await search_feedback_service.record(
        db,
        space_id=space_id,
        entity_id=body.entity_id,
        query=body.query,
        signal=body.signal,
        feedback_source=body.feedback_source,
    )
    await db.commit()
    await db.refresh(fb)
    return SearchFeedbackResponse(
        id=fb.id,
        entity_id=fb.entity_id,
        query_hash=fb.query_hash,
        signal=fb.signal,
        feedback_source=fb.feedback_source,
        created_at=fb.created_at,
    )


@router.get("/feedback/{entity_id}")
async def get_feedback_aggregate(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Get aggregated feedback for a specific block."""
    from .services import search_feedback_service

    agg = await search_feedback_service.get_aggregate(db, entity_id)
    return {"entity_id": entity_id, **agg}


# ======================== Status ========================


@router.get("/status")
async def memvault_status():
    return {"module": "memvault", "status": "active", "phase": "A"}


# ======================== Frozen Tier (Thaw) ========================


@router.get("/frozen", summary="List frozen blocks")
async def list_frozen_blocks(
    space_id: str = Query("default"),
    block_type: str | None = Query(None),
    tag: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List frozen block metadata (no content -- needs thaw)."""
    from .models import BlockFrozen

    q = select(BlockFrozen).where(
        BlockFrozen.space_id == space_id,
    )
    if block_type:
        q = q.where(BlockFrozen.block_type == block_type)
    if tag:
        q = q.where(BlockFrozen.tags.contains([tag]))

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = q.order_by(BlockFrozen.frozen_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    return {
        "items": [
            {
                "id": r.id,
                "space_id": r.space_id,
                "created_at": r.created_at,
                "frozen_at": r.frozen_at,
                "block_type": r.block_type,
                "tags": r.tags or [],
                "summary": r.summary,
                "content_size": r.content_size,
                "tier": "frozen",
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/frozen/{block_id}/thaw",
    summary="Thaw frozen block",
)
async def thaw_frozen_block(
    block_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Fetch full content from S3 for a frozen block.

    May take 1-3s for S3 download + decompression.
    """
    import json

    from src.shared.storage import (
        compute_content_hash,
        download_and_decompress,
    )

    from .models import BlockFrozen

    q = select(BlockFrozen).where(BlockFrozen.id == block_id)
    frozen = (await db.execute(q)).scalar_one_or_none()
    if not frozen:
        raise NotFoundError(
            f"Frozen block {block_id} not found",
            code="memvault.frozen_not_found",
        )

    data = await download_and_decompress(frozen.s3_uri)
    if data is None:
        raise BadRequestError(
            "Failed to retrieve frozen content from S3",
            code="memvault.thaw_failed",
        )

    # Verify integrity
    actual_hash = compute_content_hash(data)
    if actual_hash != frozen.content_hash:
        raise BadRequestError(
            f"Content hash mismatch: expected {frozen.content_hash}, got {actual_hash}",
            code="memvault.integrity_error",
        )

    content = json.loads(data.decode("utf-8"))
    return {
        "id": block_id,
        "content": content,
        "tier": "frozen",
        "frozen_at": frozen.frozen_at,
    }


# ======================== Dream Loop ========================


@router.post("/dream", summary="Run dream consolidation")
async def run_dream_consolidation(
    space_id: str = Query("default"),
    dry_run: bool = Query(True),
    force: bool = Query(False),
    use_pipeline: bool = Query(False, description="Use reactive pipeline instead of sequential"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Execute the Dream Loop: Orient → Gather Signal → Consolidate → Prune.

    Default is dry-run mode (no mutations). Set dry_run=false to apply changes.
    The dual-gate trigger (24h + 5 sessions) can be bypassed with force=true.
    """
    from .dream import run_dream

    report = await run_dream(db, space_id, dry_run=dry_run, force=force, use_pipeline=use_pipeline)
    if not dry_run and not report.skipped:
        await db.commit()
    return report.to_dict()


# ======================== Review Queue ========================


@router.get("/review-queue")
async def list_review_queue(
    space_id: str = Query(default="default"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """List pending review items: __pending__ blocks + recent dream invalidations."""
    from .kg_schemas import ReviewItem
    from .models import MemoryBlock

    offset = (page - 1) * page_size

    # Pending blocks (superseded_by = '__pending__')
    stmt = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.superseded_by == "__pending__",
        )
        .order_by(MemoryBlock.updated_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    blocks = result.scalars().all()

    items = [
        ReviewItem(
            id=b.id,
            item_type="block",
            content_preview=b.content[:200] if b.content else "",
            invalidation_reason=b.invalidation_reason,
            superseded_by=b.superseded_by,
            created_at=b.created_at,
            invalidated_at=b.invalid_at,
        )
        for b in blocks
    ]

    # Count total for pagination
    count_stmt = (
        select(func.count())
        .select_from(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.superseded_by == "__pending__",
        )
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/review-queue/{item_id}/approve")
async def approve_review(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Approve a pending review item — confirms the dream/dedup decision."""
    from sqlalchemy import update

    from .models import MemoryBlock

    result = await db.execute(
        update(MemoryBlock)
        .where(
            MemoryBlock.id == item_id,
            MemoryBlock.superseded_by == "__pending__",
        )
        .values(
            superseded_by="__approved__",
            invalidation_reason="review_approved",
            invalid_at=datetime.now(UTC),
        )
    )
    if result.rowcount == 0:
        raise NotFoundError("Review item not found or already resolved")
    await db.commit()
    return {"status": "approved", "id": item_id}


@router.post("/review-queue/{item_id}/reject")
async def reject_review(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Reject a pending review item — restores the block to active state."""
    from sqlalchemy import update

    from .models import MemoryBlock

    result = await db.execute(
        update(MemoryBlock)
        .where(
            MemoryBlock.id == item_id,
            MemoryBlock.superseded_by == "__pending__",
        )
        .values(
            superseded_by=None,
            invalidation_reason=None,
            invalid_at=None,
        )
    )
    if result.rowcount == 0:
        raise NotFoundError("Review item not found or already resolved")
    await db.commit()
    return {"status": "rejected", "id": item_id}


@router.post("/review-queue/{item_id}/defer")
async def defer_review(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    """Defer a review — mark as seen but keep pending."""
    from sqlalchemy import update

    from .models import MemoryBlock

    result = await db.execute(
        update(MemoryBlock)
        .where(
            MemoryBlock.id == item_id,
            MemoryBlock.superseded_by == "__pending__",
        )
        .values(updated_at=datetime.now(UTC))  # touch timestamp to push down in queue
    )
    if result.rowcount == 0:
        raise NotFoundError("Review item not found or already resolved")
    await db.commit()
    return {"status": "deferred", "id": item_id}


# ======================== Frontier (Worker 1) ========================


@router.get(
    "/frontier/top",
    response_model=FrontierTopResponse,
    summary="Top-N frontier candidates — what to think about next",
)
async def frontier_top(
    space_id: str = Query("default"),
    n: int = Query(5, ge=1, le=50, description="Number of candidates to return"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.read"),
):
    """Aggregate PPR proxy + out_degree + recency + knowledge_gap_bonus
    into a single per-entity score and return the top N.

    Empty graph → empty list (not an error).
    """
    from .frontier import frontier_service

    try:
        ranked = await frontier_service.compute_top(db, space_id, n=n)
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("frontier.top failed")
        raise BadRequestError(
            f"Failed to compute frontier: {exc}", code="memvault.frontier.compute_failed"
        ) from exc

    return FrontierTopResponse(
        space_id=space_id,
        n=n,
        items=[
            FrontierNodeResponse(
                entity_id=r.entity_id,
                entity_name=r.entity_name,
                score=r.score,
                ppr=r.ppr,
                out_degree=r.out_degree,
                days_since_updated=r.days_since_updated,
                knowledge_gap_bonus=r.knowledge_gap_bonus,
            )
            for r in ranked
        ],
    )
