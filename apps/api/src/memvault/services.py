"""Memvault services — CRUD + semantic search.

This is the PUBLIC API of the memvault module.
Other modules import from here, never from models.py.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.events_stub.types import MemvaultEvents
from src.shared.cache import cached
from src.shared.errors import BadRequestError, NotFoundError
from src.shared.fallback_search import (
    build_ilike_conditions,
    get_avgdl,
    score_text_match,
)
from src.shared.schemas import PaginatedResponse, PaginationParams
from src.shared.services import BaseCRUDService
from src.shared.text_utils import is_cjk, is_cjk_dominant
from src.shared.tier_config import get_threshold
from text_ops.noise import QUARANTINE_TAG, check_noise, filter_results

from .injection_guard import is_unsafe_for_injection
from .models import (
    EMBEDDING_DIM,
    BlockArchive,
    KnowledgeDomain,
    MemoryBlock,
    ProfileScore,
    SearchFeedback,
    Tag,
)
from .reranker import rerank_results
from .schemas import (
    BLOCK_TYPE_ALIASES,
    BLOCK_TYPES,
    KnowledgeDomainCreate,
    KnowledgeDomainResponse,
    KnowledgeDomainUpdate,
    MemoryBlockCreate,
    MemoryBlockResponse,
    MemoryBlockUpdate,
    ProfileScoreResponse,
    ProfileScoreUpdate,
    SearchMetadata,
    SemanticSearchResult,
    TagResponse,
)
from .scopes import parse_scopes, scopes_to_filters
from .scoring_pipeline import ScoringConfig, ScoringPipeline

logger = logging.getLogger(__name__)

# --- Greeting patterns (shared with noise_filter for should_search) ---

_GREETING_ONLY = re.compile(
    r"^(hi|hello|hey|howdy|yo|sup|greetings|good\s*(morning|afternoon|evening|night)"
    r"|你好|嗨|哈囉|早安|午安|晚安|哈嘍|嘿)[\s!.,、。\uff01]*$",
    re.IGNORECASE,
)

# CJK range pattern — from shared single source of truth

# --- Memory intent keywords (force search even for short queries) ---

# English memory-intent keywords: recollection, temporal reference, recall prompts
_MEMORY_KEYWORDS_EN = frozenset(
    {
        "remember",
        "recall",
        "forgot",
        "memory",
        "mentioned",
        "discussed",
        "talked about",
        "previous",
        "previously",
        "earlier",
        "last time",
        "before",
        "we said",
        "you said",
        "i said",
        "noted",
        "recorded",
        "what was",
        "when did",
        "where did",
    }
)

# CJK memory-intent keywords: same categories in Chinese/Japanese
_MEMORY_KEYWORDS_ZH = frozenset(
    {
        "記得",
        "記住",
        "忘了",
        "想起",
        "提過",
        "討論過",
        "說過",
        "之前",
        "上次",
        "以前",
        "前面",
        "先前",
        "剛才",
        "我們聊過",
        "有聊過",
        "講過",
        "聊到",
        "提到",
        "想想",
        "回想",
        "那個",
        "那時",
    }
)


def should_search(query: str) -> tuple[bool, str]:
    """Determine if a query warrants memory retrieval."""
    stripped = query.strip()
    lower = stripped.lower()

    # Memory keywords force search — check before length filter so short
    # CJK queries like "之前說過?" still trigger recall
    if any(kw in lower for kw in _MEMORY_KEYWORDS_EN):
        return True, "memory_keyword"
    if any(kw in stripped for kw in _MEMORY_KEYWORDS_ZH):
        return True, "memory_keyword"

    # Too short — CJK has lower threshold (each char carries more information)
    if is_cjk_dominant(stripped) and len(stripped) < 6:
        return False, "cjk_too_short"
    if not is_cjk_dominant(stripped) and len(stripped) < 10:
        return False, "too_short"

    # Pure greeting
    if _GREETING_ONLY.match(stripped):
        return False, "greeting"

    return True, "default"


# ======================== MemoryBlock Service ========================


class MemoryBlockService(
    BaseCRUDService[MemoryBlock, MemoryBlockCreate, MemoryBlockUpdate, MemoryBlockResponse]
):
    model = MemoryBlock
    audit_module = "memvault"
    audit_entity_type = "blocks"

    def before_create(self, data: MemoryBlockCreate, **kwargs: Any) -> dict:
        d = data.model_dump()
        # Normalize pipeline aliases (insight→knowledge, etc.) to canonical KAS types
        d["block_type"] = BLOCK_TYPE_ALIASES.get(d["block_type"], d["block_type"])
        if d["block_type"] not in BLOCK_TYPES:
            raise BadRequestError(
                f"Invalid block_type: {d['block_type']}",
                code="memvault.invalid_block_type",
            )
        # Defense-in-depth: Noise + Injection Guard run here as safety net.
        # Primary gate is in routes.py (runs BEFORE dedup to prevent pollution).
        # These are idempotent — adding an existing tag is a no-op.
        verdict = check_noise(d.get("content", ""))
        if verdict.is_noise:
            tags = d.get("tags") or []
            if QUARANTINE_TAG not in tags:
                tags = [*tags, QUARANTINE_TAG]
            d["tags"] = tags
        # Injection guard (defense-in-depth, primary gate in routes.py)
        unsafe, reason = is_unsafe_for_injection(d.get("content", ""))
        if unsafe:
            tags = d.get("tags") or []
            injection_tag = f"_quarantine:injection:{reason}"
            if injection_tag not in tags:
                tags = [*tags, injection_tag]
            d["tags"] = tags
            logger.warning(
                "Write-side injection guard triggered: %s (type=%s)",
                reason,
                d.get("block_type"),
            )
        return d

    event_types = {"created": MemvaultEvents.MEMORY_STORED}
    event_id_alias = "block_id"
    event_fields = ("content", "block_type", "tags", "source_session")

    def to_response(self, instance: MemoryBlock) -> MemoryBlockResponse:
        return MemoryBlockResponse(
            id=instance.id,
            space_id=instance.space_id,
            created_by=instance.created_by,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            content=instance.content,
            block_type=instance.block_type,
            tags=instance.tags or [],
            source_session=instance.source_session,
            confidence=instance.confidence or 0.0,
            invalid_at=instance.invalid_at,
            superseded_by=instance.superseded_by,
            invalidation_reason=instance.invalidation_reason,
        )

    async def invalidate_block(
        self, db: AsyncSession, block_id: str, superseded_by_id: str, reason: str = "superseded"
    ) -> None:
        """Mark a block as invalid (superseded by newer knowledge). Does NOT delete."""
        from datetime import UTC, datetime

        block = await self.get(db, block_id)
        if not block:
            return
        block.invalid_at = datetime.now(UTC)
        block.superseded_by = superseded_by_id
        block.invalidation_reason = reason

    @staticmethod
    def _apply_date_filter(q, date_from=None, date_to=None):
        if date_from:
            q = q.where(MemoryBlock.created_at >= date_from)
        if date_to:
            q = q.where(MemoryBlock.created_at <= date_to)
        return q

    async def list(
        self,
        db: AsyncSession,
        space_id: str,
        pagination: PaginationParams | None = None,
        date_from=None,
        date_to=None,
    ) -> PaginatedResponse[MemoryBlockResponse]:
        p = pagination or PaginationParams()
        base = select(MemoryBlock).where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at == None,  # noqa: E711
        )
        base = self._apply_date_filter(base, date_from, date_to)
        count_q = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_q)).scalar_one()
        q = (
            base.order_by(MemoryBlock.created_at.desc())
            .offset((p.page - 1) * p.page_size)
            .limit(p.page_size)
        )
        rows = (await db.execute(q)).scalars().all()
        return PaginatedResponse[MemoryBlockResponse](
            items=[self.to_response(r) for r in rows],
            total=total,
            page=p.page,
            page_size=p.page_size,
        )

    async def list_by_tags(
        self,
        db: AsyncSession,
        space_id: str,
        tags: list[str],
        pagination: PaginationParams | None = None,
        date_from=None,
        date_to=None,
    ) -> PaginatedResponse[MemoryBlockResponse]:
        """List blocks that contain ALL specified tags."""
        p = pagination or PaginationParams()
        base = select(MemoryBlock).where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.tags.contains(tags),
            MemoryBlock.deleted_at == None,  # noqa: E711
        )
        base = self._apply_date_filter(base, date_from, date_to)
        count_q = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_q)).scalar_one()
        q = base.offset((p.page - 1) * p.page_size).limit(p.page_size)
        rows = (await db.execute(q)).scalars().all()
        return PaginatedResponse[MemoryBlockResponse](
            items=[self.to_response(r) for r in rows],
            total=total,
            page=p.page,
            page_size=p.page_size,
        )

    async def list_by_type(
        self,
        db: AsyncSession,
        space_id: str,
        block_type: str,
        pagination: PaginationParams | None = None,
        date_from=None,
        date_to=None,
    ) -> PaginatedResponse[MemoryBlockResponse]:
        """List blocks filtered by block_type."""
        p = pagination or PaginationParams()
        base = select(MemoryBlock).where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.block_type == block_type,
            MemoryBlock.deleted_at == None,  # noqa: E711
        )
        base = self._apply_date_filter(base, date_from, date_to)
        count_q = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_q)).scalar_one()
        q = base.offset((p.page - 1) * p.page_size).limit(p.page_size)
        rows = (await db.execute(q)).scalars().all()
        return PaginatedResponse[MemoryBlockResponse](
            items=[self.to_response(r) for r in rows],
            total=total,
            page=p.page,
            page_size=p.page_size,
        )

    async def semantic_search(
        self,
        db: AsyncSession,
        space_id: str,
        query_embedding: list[float],
        top_k: int = 10,
        threshold: float = 0.3,
        tags: list[str] | None = None,
        block_type: str | None = None,
        include_warm: bool = True,
        query: str | None = None,
        scoring_config: ScoringConfig | None = None,
        scope: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        keywords: list[str] | None = None,
        intent: str = "unknown",
    ) -> tuple[list[SemanticSearchResult], SearchMetadata]:
        """Text-based fallback search (keyword + warm tier).

        Only used as emergency fallback when Qdrant infrastructure is unavailable.
        Primary search should go through qdrant_search().

        pgvector embedding columns were removed in Qdrant migration g9h0i1j2k3l4,
        so this method no longer performs vector similarity — it relies on
        PostgreSQL keyword search (tsvector / ILIKE) and warm-tier text search.

        Returns (results, metadata) tuple.
        """
        if query_embedding is None:
            # MLX disabled or oMLX unavailable — skip vector check, go straight to keyword fallback
            meta = SearchMetadata(vector_used=False, scope=scope)
            extra_filters = scopes_to_filters(parse_scopes(scope)) if scope else []
            if date_from:
                extra_filters.append(MemoryBlock.created_at >= date_from)
            if date_to:
                extra_filters.append(MemoryBlock.created_at <= date_to)
            results: list[SemanticSearchResult] = []
            if query:
                results = await self._keyword_search(
                    db,
                    space_id,
                    query,
                    top_k,
                    tags,
                    block_type,
                    extra_filters=extra_filters,
                    keywords=keywords,
                )
                meta.keyword_used = True
            return results[:top_k], meta

        if len(query_embedding) != EMBEDDING_DIM:
            raise BadRequestError(
                f"Embedding must be {EMBEDDING_DIM}d, got {len(query_embedding)}d",
                code="memvault.invalid_embedding_dim",
            )

        meta = SearchMetadata(vector_used=False, scope=scope)

        # Defense ⑦: Parse scope and build extra filters
        extra_filters = scopes_to_filters(parse_scopes(scope)) if scope else []

        # Time range pre-filters
        if date_from:
            extra_filters.append(MemoryBlock.created_at >= date_from)
        if date_to:
            extra_filters.append(MemoryBlock.created_at <= date_to)

        results: list[SemanticSearchResult] = []

        # Keyword search (tsvector for English, ILIKE for CJK)
        if query:
            keyword_results = await self._keyword_search(
                db,
                space_id,
                query,
                top_k,
                tags,
                block_type,
                extra_filters=extra_filters,
                keywords=keywords,
            )
            meta.keyword_used = True
            results = keyword_results

        # Warm tier: text-based augmentation for older blocks
        if include_warm and query and len(results) < top_k:
            warm_results = await self._warm_tier_search(
                db,
                space_id,
                query,
                top_k - len(results),
                tags,
                block_type,
                extra_filters=extra_filters,
            )
            results.extend(warm_results)

        # Phase A1 + A2: Noise filter on results + Scoring Pipeline
        results, _ = filter_results(results)

        # Fetch ORM rows for access tracking (G6)
        block_ids = [r.block.id for r in results]
        orm_map: dict[str, MemoryBlock] = {}
        if block_ids:
            orm_q = select(MemoryBlock).where(MemoryBlock.id.in_(block_ids))
            for row in (await db.execute(orm_q)).scalars().all():
                orm_map[row.id] = row

        # Convert to scoring pipeline format
        pipeline = ScoringPipeline(scoring_config)
        scored_dicts = [
            {
                "block": r.block,
                "score": r.score,
                "content": r.block.content,
                "created_at": r.block.created_at,
                "confidence": r.block.confidence,
                "embedding": None,
                "access_count": getattr(orm_map.get(r.block.id), "access_count", 0) or 0,
                "last_accessed_at": getattr(orm_map.get(r.block.id), "last_accessed_at", None),
            }
            for r in results
        ]

        scored_dicts, scoring_meta = await pipeline.apply(scored_dicts, query_embedding)

        # Phase C2: Optional cross-encoder reranking (attention-gated)
        if query:
            scored_dicts, reranked, gate_reason = await rerank_results(
                query,
                scored_dicts,
                intent=intent,
            )
            if reranked:
                meta.reranker_used = True
            elif gate_reason:
                meta.reranker_gated = True
                meta.reranker_gate_reason = gate_reason

        # Update metadata
        meta.scoring_applied = True
        meta.stages_applied = scoring_meta.stages_applied
        meta.stages_skipped = scoring_meta.stages_skipped
        meta.noise_filtered = scoring_meta.noise_filtered
        meta.input_count = scoring_meta.input_count
        meta.output_count = scoring_meta.output_count

        # Convert back to SemanticSearchResult
        final_results = [
            SemanticSearchResult(
                block=d["block"],
                score=round(d["score"], 4),
            )
            for d in scored_dicts[:top_k]
        ]

        # G6: Record access for returned blocks (fire-and-forget, best-effort)
        if final_results:
            from src.shared.access_tracker import record_access

            for sr in final_results:
                try:
                    await record_access(sr.block.id, db)
                except Exception:  # noqa: S110
                    pass  # never block search results for tracking failures

        return final_results, meta

    async def qdrant_search(
        self,
        db: AsyncSession,
        space_id: str,
        query: str,
        query_embedding: list[float],
        top_k: int = 10,
        tags: list[str] | None = None,
        block_type: str | None = None,
        scoring_config: ScoringConfig | None = None,
        scope: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        intent: str = "unknown",
    ) -> tuple[list[SemanticSearchResult], SearchMetadata] | None:
        """Search via Qdrant hybrid (BM25 + dense) with full scoring pipeline.

        Returns None if Qdrant is unavailable (caller should fall back to semantic_search).
        """
        from src.shared.qdrant_client import is_available as qdrant_available
        from src.shared.qdrant_search import hybrid_search as qdrant_hybrid_search
        from src.shared.search_types import SearchConfig as QdrantSearchConfig

        if not await qdrant_available():
            return None

        meta = SearchMetadata(vector_used=True, scope=scope)

        # Build Qdrant search config
        config = QdrantSearchConfig(
            top_k=top_k * 3,  # over-fetch for scoring pipeline
            score_threshold=0.0,
            service_ids=["memvault"],
            tag_filter=tags,
        )
        qdrant_results, qdrant_meta = await qdrant_hybrid_search(
            query, space_id, config, with_vectors=True
        )

        if not qdrant_results:
            # Qdrant is available but found nothing — return empty results
            meta.backend = "qdrant"
            meta.input_count = 0
            meta.output_count = 0
            return [], meta

        meta.keyword_used = qdrant_meta.sparse_used

        # Fetch full records from DB by entity_id for scoring pipeline
        entity_ids = [r.entity_id for r in qdrant_results]
        q = select(MemoryBlock).where(
            MemoryBlock.id.in_(entity_ids),
            MemoryBlock.deleted_at == None,  # noqa: E711
        )
        if date_from:
            q = q.where(MemoryBlock.created_at >= date_from)
        if date_to:
            q = q.where(MemoryBlock.created_at <= date_to)
        if block_type:
            q = q.where(MemoryBlock.block_type == block_type)

        rows = (await db.execute(q)).scalars().all()
        block_map = {str(b.id): b for b in rows}

        # Build score map from Qdrant results
        score_map = {r.entity_id: r.score for r in qdrant_results}

        # Build embedding map from Qdrant results (returned when with_vectors=True).
        # Used by SemanticBoostOp and PairwiseDedupOp in the scoring pipeline.
        emb_map: dict[str, list[float]] = {
            r.entity_id: r.embedding for r in qdrant_results if r.embedding is not None
        }
        if not emb_map:
            logger.warning(
                "qdrant_search: no embeddings returned — SemanticBoostOp and PairwiseDedupOp will be"
                " skipped. Ensure Qdrant collection stores dense vectors."
            )

        # Fetch feedback aggregates for scoring pipeline (best-effort)
        feedback_map: dict[str, int] = {}
        try:
            feedback_map = await search_feedback_service.get_bulk_aggregates(
                db, list(block_map.keys())
            )
        except Exception:
            logger.debug("Feedback aggregate fetch failed, skipping", exc_info=True)

        # Convert to scoring pipeline format
        scored_dicts = []
        for eid in entity_ids:
            block = block_map.get(eid)
            if not block:
                continue
            scored_dicts.append(
                {
                    "block": self.to_response(block),
                    "score": score_map.get(eid, 0.0),
                    "content": block.content,
                    "created_at": block.created_at,
                    "confidence": block.confidence,
                    "embedding": emb_map.get(eid),
                    "access_count": block.access_count or 0,
                    "last_accessed_at": block.last_accessed_at,
                    "feedback_net": feedback_map.get(eid, 0),
                }
            )

        # Apply full scoring pipeline
        pipeline = ScoringPipeline(scoring_config)
        scored_dicts, scoring_meta = await pipeline.apply(scored_dicts, query_embedding)

        # Optional cross-encoder reranking (attention-gated)
        scored_dicts, reranked, gate_reason = await rerank_results(
            query,
            scored_dicts,
            intent=intent,
        )
        if reranked:
            meta.reranker_used = True
        elif gate_reason:
            meta.reranker_gated = True
            meta.reranker_gate_reason = gate_reason

        meta.scoring_applied = True
        meta.stages_applied = scoring_meta.stages_applied
        meta.stages_skipped = scoring_meta.stages_skipped
        meta.noise_filtered = scoring_meta.noise_filtered
        meta.input_count = scoring_meta.input_count
        meta.output_count = scoring_meta.output_count
        meta.backend = "qdrant"

        final = [
            SemanticSearchResult(
                block=d["block"],
                score=round(d["score"], 4),
            )
            for d in scored_dicts[:top_k]
        ]

        # G6: Record access for returned blocks (fire-and-forget, best-effort)
        if final:
            from src.shared.access_tracker import record_access

            for sr in final:
                try:
                    await record_access(sr.block.id, db)
                except Exception:  # noqa: S110
                    pass

        return final, meta

    async def _keyword_search(
        self,
        db: AsyncSession,
        space_id: str,
        query: str,
        top_k: int,
        tags: list[str] | None = None,
        block_type: str | None = None,
        extra_filters: list | None = None,
        keywords: list[str] | None = None,
    ) -> list[SemanticSearchResult]:
        """PostgreSQL keyword search.

        Uses tsvector for English text; jieba multi-term ILIKE for CJK.
        When HyDE-expanded keywords are provided, they augment the search terms.
        """
        if is_cjk(query):
            # CJK: jieba multi-term ILIKE with BM25-lite scoring
            # When HyDE keywords are available, use them for richer search conditions
            if keywords:
                from sqlalchemy import or_

                # Original conditions + OR-expanded conditions from HyDE keywords
                base_conditions = build_ilike_conditions(query, MemoryBlock.content)
                extra_kw_conditions = [
                    MemoryBlock.content.ilike(f"%{kw}%") for kw in keywords if len(kw) >= 2
                ]
                if extra_kw_conditions:
                    conditions = [or_(*base_conditions, *extra_kw_conditions)]
                else:
                    conditions = base_conditions
            else:
                conditions = build_ilike_conditions(query, MemoryBlock.content)
            q = (
                select(MemoryBlock)
                .where(
                    MemoryBlock.space_id == space_id,
                    *conditions,
                    MemoryBlock.deleted_at == None,  # noqa: E711
                )
                .order_by(MemoryBlock.updated_at.desc())
                .limit(top_k)
            )
        else:
            # English: use tsvector + ts_rank_cd
            # When HyDE keywords are available, combine them with original query
            search_text = query
            if keywords:
                # Merge HyDE keywords into the search text for richer tsvector matching
                extra_terms = " ".join(kw for kw in keywords if kw.lower() not in query.lower())
                if extra_terms:
                    search_text = f"{query} {extra_terms}"
            ts_query = func.plainto_tsquery("english", search_text)
            ts_vector = func.to_tsvector("english", MemoryBlock.content)
            rank = func.ts_rank_cd(ts_vector, ts_query).label("rank")
            q = (
                select(MemoryBlock, rank)
                .where(
                    MemoryBlock.space_id == space_id,
                    ts_vector.op("@@")(ts_query),
                    MemoryBlock.deleted_at == None,  # noqa: E711
                )
                .order_by(rank.desc())
                .limit(top_k)
            )

        if tags:
            q = q.where(MemoryBlock.tags.contains(tags))
        if block_type:
            q = q.where(MemoryBlock.block_type == block_type)
        for f in extra_filters or []:
            q = q.where(f)

        rows = (await db.execute(q)).all()

        avgdl = get_avgdl("memvault")
        results = []
        for row in rows:
            if is_cjk(query):
                block = row
                # BM25-lite scoring instead of hardcoded 0.5
                score = score_text_match(query, block.content, tier="hot", avgdl=avgdl)
            else:
                block = row.MemoryBlock
                score = float(row.rank) if row.rank else 0.3
            results.append(
                SemanticSearchResult(
                    block=self.to_response(block),
                    score=round(score, 4),
                )
            )
        return results

    async def _rrf_fuse(
        self,
        vector_results: list[SemanticSearchResult],
        keyword_results: list[SemanticSearchResult],
        k: int = 60,
        keyword_boost: float = 0.15,
    ) -> list[SemanticSearchResult]:
        """Reciprocal Rank Fusion: combine vector and keyword results."""
        scores: dict[str, float] = {}
        best_result: dict[str, SemanticSearchResult] = {}

        # Score from vector results
        for rank, r in enumerate(vector_results):
            block_id = r.block.id
            scores[block_id] = scores.get(block_id, 0) + 1.0 / (k + rank)
            if block_id not in best_result or r.score > best_result[block_id].score:
                best_result[block_id] = r

        # Score from keyword results with boost
        keyword_ids = set()
        for rank, r in enumerate(keyword_results):
            block_id = r.block.id
            keyword_ids.add(block_id)
            scores[block_id] = scores.get(block_id, 0) + (1.0 / (k + rank) * (1 + keyword_boost))
            if block_id not in best_result or r.score > best_result[block_id].score:
                best_result[block_id] = r

        # Sort by fused score, but keep original similarity as the result score
        # (RRF scores are tiny ~0.01 and unsuitable for downstream min_score filtering)
        sorted_ids = sorted(scores, key=lambda bid: scores[bid], reverse=True)
        return [
            SemanticSearchResult(
                block=best_result[bid].block,
                score=round(best_result[bid].score, 4),
            )
            for bid in sorted_ids
        ]

    async def _warm_tier_search(
        self,
        db: AsyncSession,
        space_id: str,
        query: str,
        remaining: int,
        tags: list[str] | None,
        block_type: str | None,
        extra_filters: list | None = None,
    ) -> list[SemanticSearchResult]:
        """Search warm-tier blocks (no HNSW, still in main table).

        Warm tier: hot_days < age <= warm_days.
        Uses jieba multi-term search for CJK with BM25-lite scoring.
        """
        tier = get_threshold("memvault")
        now = datetime.now(UTC)
        hot_cutoff = now - timedelta(days=tier.hot_days)
        warm_cutoff = now - timedelta(days=tier.warm_days)

        # CJK-aware conditions (jieba multi-term instead of single ILIKE)
        conditions = build_ilike_conditions(query, MemoryBlock.content)

        q = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                *conditions,
                MemoryBlock.created_at < hot_cutoff,
                MemoryBlock.created_at >= warm_cutoff,
            )
            .order_by(MemoryBlock.updated_at.desc())
            .limit(remaining)
        )
        if tags:
            q = q.where(MemoryBlock.tags.contains(tags))
        if block_type:
            q = q.where(MemoryBlock.block_type == block_type)
        for f in extra_filters or []:
            q = q.where(f)

        rows = (await db.execute(q)).scalars().all()
        avgdl = get_avgdl("memvault")
        return [
            SemanticSearchResult(
                block=self.to_response(r),
                score=round(score_text_match(query, r.content, tier="warm", avgdl=avgdl), 4),
            )
            for r in rows
        ]

    async def text_search(
        self,
        db: AsyncSession,
        space_id: str,
        query: str,
        top_k: int = 10,
        include_archived: bool = False,
        include_warm: bool = True,
        scope: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[SemanticSearchResult]:
        """Fallback text search — CJK-aware jieba multi-term + BM25-lite scoring.

        Tier-aware search with configurable scoring:
          Hot  (age <= hot_days): BM25-lite score
          Warm (hot_days < age <= warm_days): BM25-lite * warm_decay
          Cold (archive table, include_archived): BM25-lite * cold_decay
        """
        tier = get_threshold("memvault")
        now = datetime.now(UTC)
        hot_cutoff = now - timedelta(days=tier.hot_days)
        warm_cutoff = now - timedelta(days=tier.warm_days)
        avgdl = get_avgdl("memvault")

        # Defense ⑦: Parse scope filters + time range pre-filters
        extra_filters = scopes_to_filters(parse_scopes(scope)) if scope else []
        if date_from:
            extra_filters.append(MemoryBlock.created_at >= date_from)
        if date_to:
            extra_filters.append(MemoryBlock.created_at <= date_to)

        # CJK-aware conditions (jieba multi-term instead of single ILIKE)
        conditions = build_ilike_conditions(query, MemoryBlock.content)

        # --- Hot tier: recent blocks (full index coverage) ---
        hot_q = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                *conditions,
                MemoryBlock.created_at >= hot_cutoff,
            )
            .order_by(MemoryBlock.updated_at.desc())
            .limit(top_k)
        )
        for f in extra_filters:
            hot_q = hot_q.where(f)
        hot_rows = (await db.execute(hot_q)).scalars().all()
        results: list[SemanticSearchResult] = [
            SemanticSearchResult(
                block=self.to_response(r),
                score=round(score_text_match(query, r.content, tier="hot", avgdl=avgdl), 4),
            )
            for r in hot_rows
        ]

        # --- Warm tier: older blocks still in main table ---
        if include_warm and len(results) < top_k:
            remaining = top_k - len(results)
            warm_q = (
                select(MemoryBlock)
                .where(
                    MemoryBlock.space_id == space_id,
                    *conditions,
                    MemoryBlock.created_at < hot_cutoff,
                    MemoryBlock.created_at >= warm_cutoff,
                )
                .order_by(MemoryBlock.updated_at.desc())
                .limit(remaining)
            )
            for f in extra_filters:
                warm_q = warm_q.where(f)
            warm_rows = (await db.execute(warm_q)).scalars().all()
            results.extend(
                [
                    SemanticSearchResult(
                        block=self.to_response(r),
                        score=round(
                            score_text_match(query, r.content, tier="warm", avgdl=avgdl),
                            4,
                        ),
                    )
                    for r in warm_rows
                ]
            )

        # --- Cold tier: archive table ---
        if include_archived and len(results) < top_k:
            remaining = top_k - len(results)
            cold_conditions = build_ilike_conditions(query, BlockArchive.content)
            archive_q = (
                select(BlockArchive)
                .where(
                    BlockArchive.space_id == space_id,
                    *cold_conditions,
                    ~BlockArchive.content.like("s3://%"),
                )
                .order_by(BlockArchive.created_at.desc())
                .limit(remaining)
            )
            archive_rows = (await db.execute(archive_q)).scalars().all()
            results.extend(
                [
                    SemanticSearchResult(
                        block=MemoryBlockResponse(
                            id=r.id,
                            space_id=r.space_id,
                            created_by=r.created_by,
                            created_at=r.created_at,
                            updated_at=r.updated_at,
                            content=r.content,
                            block_type=r.block_type,
                            tags=r.tags or [],
                            source_session=r.source_session,
                            confidence=r.confidence or 0.0,
                        ),
                        score=round(
                            score_text_match(query, r.content, tier="cold", avgdl=avgdl),
                            4,
                        ),
                    )
                    for r in archive_rows
                ]
            )

        # G6: Record access for returned blocks (fire-and-forget, best-effort)
        # Only for hot/warm results (archive blocks don't have access_count)
        if results:
            from src.shared.access_tracker import record_access

            for sr in results:
                try:
                    await record_access(sr.block.id, db)
                except Exception:  # noqa: S110
                    pass

        return results

    async def find_by_source_session(
        self, db: AsyncSession, space_id: str, source_session: str
    ) -> MemoryBlockResponse | None:
        """Return the first block matching source_session for idempotency checks."""
        q = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                MemoryBlock.source_session == source_session,
                MemoryBlock.deleted_at == None,  # noqa: E711
            )
            .limit(1)
        )
        row = (await db.execute(q)).scalars().first()
        return self.to_response(row) if row else None

    async def update_embedding(
        self, db: AsyncSession, block_id: str, embedding: list[float] | None = None
    ) -> None:
        """Re-index a block to Qdrant.

        Drift-fix: post-Qdrant migration the `embedding` argument is ignored —
        Qdrant re-embeds from `block.content` (single source of truth). The
        argument is kept for caller compatibility and may be removed once all
        callers stop passing pre-computed vectors.
        """
        # Local imports — avoid shadowing the `qdrant_search` method on this class.
        from src.shared.qdrant_search import index_document as _qdrant_index_document
        from src.shared.search_types import IndexDocument

        if embedding is not None and len(embedding) != EMBEDDING_DIM:
            raise BadRequestError(
                f"Embedding must be {EMBEDDING_DIM}d",
                code="memvault.invalid_embedding_dim",
            )

        # Fetch block to verify existence and pull content
        q = select(MemoryBlock).where(
            MemoryBlock.id == block_id,
            MemoryBlock.deleted_at.is_(None),
        )
        block = (await db.execute(q)).scalars().first()
        if block is None:
            raise NotFoundError("Block not found", code="memvault.block_not_found")

        ok = await _qdrant_index_document(
            IndexDocument(
                service_id="memvault",
                entity_id=block.id,
                entity_type="block",
                space_id=block.space_id,
                content=block.content,
                tags=block.tags or [],
                created_at=block.created_at,
                updated_at=block.updated_at,
                metadata={
                    "block_type": block.block_type,
                    "confidence": block.confidence,
                    "source_session": block.source_session,
                },
            )
        )
        if not ok:
            logger.warning(
                "Qdrant index failed for block %s — degrade silently", block_id
            )


# ======================== Tag Service ========================


class TagService:
    """Lightweight tag aggregation — no BaseCRUD needed."""

    @cached("memvault", "list_tags", ttl=1800, key_params=("space_id",))
    async def list_tags(self, db: AsyncSession, space_id: str) -> list[TagResponse]:
        """List all tags for a space, ordered by usage count."""
        q = select(Tag).where(Tag.space_id == space_id).order_by(Tag.usage_count.desc())
        rows = (await db.execute(q)).scalars().all()
        return [TagResponse(name=r.name, usage_count=r.usage_count) for r in rows]

    async def sync_tags(self, db: AsyncSession, space_id: str) -> int:
        """Rebuild tag counts from blocks. Returns number of tags synced."""
        # Unnest all tags from blocks and count
        tag_counts = (
            select(
                func.unnest(MemoryBlock.tags).label("tag_name"),
                func.count().label("cnt"),
            )
            .where(MemoryBlock.space_id == space_id)
            .group_by(text("tag_name"))
            .subquery()
        )

        # Delete existing tags for this space
        await db.execute(delete(Tag).where(Tag.space_id == space_id))

        # Insert fresh counts
        rows = (await db.execute(select(tag_counts))).all()
        for row in rows:
            db.add(Tag(space_id=space_id, name=row.tag_name, usage_count=row.cnt))
        await db.flush()
        return len(rows)


# ======================== KnowledgeDomain Service ========================


class KnowledgeDomainService(
    BaseCRUDService[
        KnowledgeDomain,
        KnowledgeDomainCreate,
        KnowledgeDomainUpdate,
        KnowledgeDomainResponse,
    ]
):
    model = KnowledgeDomain
    audit_module = "memvault"
    audit_entity_type = "knowledge_domains"

    def to_response(self, instance: KnowledgeDomain) -> KnowledgeDomainResponse:
        return KnowledgeDomainResponse(
            id=instance.id,
            space_id=instance.space_id,
            created_by=instance.created_by,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            name=instance.name,
            description=instance.description,
            maturity=instance.maturity,
            block_count=instance.block_count,
        )


# ======================== ProfileScore Service ========================


class ProfileScoreService:
    """Single profile score per space — K/A/S aggregate scores."""

    @cached("memvault", "profile_score", ttl=1800, key_params=("space_id",))
    async def get_by_space(self, db: AsyncSession, space_id: str) -> ProfileScoreResponse | None:
        q = select(ProfileScore).where(ProfileScore.space_id == space_id)
        instance = (await db.execute(q)).scalar_one_or_none()
        if not instance:
            return None
        return ProfileScoreResponse(
            id=instance.id,
            space_id=instance.space_id,
            created_by=instance.created_by,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            knowledge_score=instance.knowledge_score,
            attitude_score=instance.attitude_score,
        )

    async def upsert(
        self,
        db: AsyncSession,
        space_id: str,
        data: ProfileScoreUpdate,
        user_id: str | None = None,
    ) -> ProfileScoreResponse:
        q = select(ProfileScore).where(ProfileScore.space_id == space_id)
        existing = (await db.execute(q)).scalar_one_or_none()
        if existing:
            updates = data.model_dump(exclude_unset=True)
            for key, value in updates.items():
                setattr(existing, key, value)
            await db.flush()
            await db.refresh(existing)  # reload server-side onupdate fields
            return self.to_response(existing)
        # Create new
        profile = ProfileScore(
            space_id=space_id,
            created_by=user_id,
            knowledge_score=data.knowledge_score or 0.0,
            attitude_score=data.attitude_score or 0.0,
        )
        db.add(profile)
        await db.flush()
        await db.refresh(profile)  # reload server-side defaults
        return self.to_response(profile)

    def to_response(self, instance: ProfileScore) -> ProfileScoreResponse:
        return ProfileScoreResponse(
            id=instance.id,
            space_id=instance.space_id,
            created_by=instance.created_by,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            knowledge_score=instance.knowledge_score,
            attitude_score=instance.attitude_score,
        )


# ======================== SearchFeedback Service ========================


class SearchFeedbackService:
    """Explicit relevance feedback for search results — enables closed-loop learning."""

    async def record(
        self,
        db: AsyncSession,
        space_id: str,
        entity_id: str,
        query: str,
        signal: str,
        feedback_source: str = "agent",
    ) -> SearchFeedback:
        """Record a feedback signal for a search result."""
        import hashlib

        query_hash = hashlib.sha256(query.encode()).hexdigest()
        fb = SearchFeedback(
            space_id=space_id,
            entity_id=entity_id,
            query_hash=query_hash,
            signal=signal,
            feedback_source=feedback_source,
        )
        db.add(fb)
        await db.flush()
        await db.refresh(fb)
        return fb

    async def get_aggregate(self, db: AsyncSession, entity_id: str) -> dict[str, int]:
        """Get aggregated feedback counts for an entity.

        Returns {"positive_count": N, "negative_count": M, "net_signal": N-M}.
        """
        q = select(
            func.count().filter(SearchFeedback.signal == "positive").label("pos"),
            func.count().filter(SearchFeedback.signal == "negative").label("neg"),
        ).where(
            SearchFeedback.entity_id == entity_id,
            SearchFeedback.deleted_at == None,  # noqa: E711
        )
        row = (await db.execute(q)).one()
        pos = row.pos or 0
        neg = row.neg or 0
        return {"positive_count": pos, "negative_count": neg, "net_signal": pos - neg}

    async def get_bulk_aggregates(self, db: AsyncSession, entity_ids: list[str]) -> dict[str, int]:
        """Get net feedback signal for multiple entities in one query.

        Returns {entity_id: net_signal, ...}. Missing entities have 0.
        """
        if not entity_ids:
            return {}

        q = (
            select(
                SearchFeedback.entity_id,
                func.sum(
                    func.cast(
                        text("CASE WHEN signal = 'positive' THEN 1 ELSE -1 END"),
                        Integer,
                    )
                ).label("net"),
            )
            .where(
                SearchFeedback.entity_id.in_(entity_ids),
                SearchFeedback.deleted_at == None,  # noqa: E711
            )
            .group_by(SearchFeedback.entity_id)
        )
        rows = (await db.execute(q)).all()
        return {row.entity_id: int(row.net or 0) for row in rows}


# ======================== Module-level singletons ========================

memory_block_service = MemoryBlockService()
tag_service = TagService()
knowledge_domain_service = KnowledgeDomainService()
profile_score_service = ProfileScoreService()
search_feedback_service = SearchFeedbackService()
