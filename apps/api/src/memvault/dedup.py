"""G1+G8: Block-level deduplication — prevent memory bloat from duplicate content.

Stage 1: Vector similarity pre-filter (fast, DB-level)
Stage 2: Content comparison decision (merge/skip/create) — now category-aware

Pure types, enums, and thresholds are in src.shared.dedup_types.
Pure text overlap is in text_ops.overlap.
Pure content merge is in text_ops.merge.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.dedup_types import (
    CONTENT_OVERLAP_RATIO,
    DEDUP_SIMILARITY_THRESHOLD,
    CategoryDedupRule,
    DedupBehavior,
    DedupDecision,
    DedupResult,
    get_dedup_rule,
)
from src.shared.dedup_types import (
    conflict_dedup_threshold as _conflict_dedup_threshold,
)
from src.shared.qdrant_client import is_available as qdrant_available
from src.shared.qdrant_search import hybrid_search
from src.shared.search_types import SearchConfig
from text_ops.overlap import jaccard_word_overlap as _content_overlap

from .models import MemoryBlock

logger = logging.getLogger(__name__)

# Re-export for backward compatibility (routes.py, dream.py import from here)
__all__ = [
    "CONTENT_OVERLAP_RATIO",
    "DEDUP_SIMILARITY_THRESHOLD",
    "CategoryDedupRule",
    "DedupBehavior",
    "DedupDecision",
    "DedupResult",
    "check_duplicate",
    "find_similar_blocks",
    "get_dedup_rule",
]


async def find_similar_blocks(
    db: AsyncSession,
    space_id: str,
    embedding: list[float],
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
    limit: int = 3,
    content: str | None = None,
) -> list[tuple[str, str, float]]:
    """Find existing blocks with similar embeddings.

    Returns list of (block_id, content, similarity) tuples.

    Primary path: Qdrant hybrid search (dense + BM25 fusion) when available.
    """
    if content and await qdrant_available():
        try:
            config = SearchConfig(
                top_k=limit,
                score_threshold=threshold,
                service_ids=["memvault"],
                use_sparse=True,
                use_dense=True,
            )
            results, _meta = await hybrid_search(content, space_id, config)
            if results:
                block_ids = [r.entity_id for r in results]
                q_content = select(MemoryBlock.id, MemoryBlock.content).where(
                    MemoryBlock.id.in_(block_ids),
                    MemoryBlock.deleted_at == None,  # noqa: E711
                )
                rows_map = {str(row[0]): str(row[1]) for row in (await db.execute(q_content)).all()}
                tuples = []
                for r in results:
                    block_content = rows_map.get(r.entity_id)
                    if block_content is not None:
                        tuples.append((r.entity_id, block_content, float(r.score)))
                if tuples:
                    return tuples
        except Exception:
            logger.warning("Qdrant dedup search failed, falling back", exc_info=True)

    return []


async def check_duplicate(
    db: AsyncSession,
    space_id: str,
    content: str,
    embedding: list[float],
    threshold: float = DEDUP_SIMILARITY_THRESHOLD,
    block_type: str | None = None,
) -> DedupResult:
    """Two-stage, category-aware dedup check before block creation."""
    rule = get_dedup_rule(block_type)
    effective_threshold = threshold if threshold != DEDUP_SIMILARITY_THRESHOLD else rule.threshold

    # APPEND_ONLY: skip all similarity checks, always create
    if rule.behavior == DedupBehavior.APPEND_ONLY:
        return DedupResult(
            decision=DedupDecision.CREATE,
            reason=f"append_only ({block_type})",
            block_type=block_type,
        )

    similar = await find_similar_blocks(
        db, space_id, embedding, effective_threshold, content=content
    )

    if not similar:
        return DedupResult(
            decision=DedupDecision.CREATE,
            reason="no_similar_found",
            block_type=block_type,
        )

    best_id, best_content, best_sim = similar[0]

    # ALWAYS_MERGE: any candidate above threshold → merge immediately
    if rule.behavior == DedupBehavior.ALWAYS_MERGE:
        return DedupResult(
            decision=DedupDecision.MERGE,
            existing_block_id=best_id,
            similarity=best_sim,
            reason=f"always_merge ({block_type}, sim={best_sim:.3f})",
            block_type=block_type,
        )

    # MERGE_IF_SIMILAR: original two-stage content comparison
    if best_sim > 0.95:
        overlap = _content_overlap(content, best_content)
        if overlap > CONTENT_OVERLAP_RATIO:
            return DedupResult(
                decision=DedupDecision.SKIP,
                existing_block_id=best_id,
                similarity=best_sim,
                reason=f"near_identical (sim={best_sim:.3f}, overlap={overlap:.2f})",
                block_type=block_type,
            )

    # LLM conflict arbitration for uncertain zone
    if best_sim >= _conflict_dedup_threshold(block_type or "general"):
        try:
            from src.shared.conflict import ConflictDecision

            from .conflict_resolver import resolve_conflict

            # Fetch existing block's created_at for temporal context
            existing_ts = None
            ts_row = (
                await db.execute(
                    select(MemoryBlock.created_at).where(MemoryBlock.id == best_id)
                )
            ).scalar_one_or_none()
            if ts_row:
                existing_ts = ts_row

            cr = await resolve_conflict(
                new_content=content,
                existing_content=best_content,
                existing_block_id=best_id,
                block_type=block_type or "general",
                similarity=best_sim,
                existing_timestamp=existing_ts,
            )
            decision_map = {
                ConflictDecision.MERGE: DedupDecision.MERGE,
                ConflictDecision.SUPERSEDE: DedupDecision.SUPERSEDE,
                ConflictDecision.COEXIST: DedupDecision.CREATE,
            }
            return DedupResult(
                decision=decision_map.get(cr.decision, DedupDecision.CREATE),
                existing_block_id=best_id,
                similarity=best_sim,
                reason=f"conflict_resolver:{cr.decision.value} ({cr.reason})",
                block_type=block_type,
            )
        except Exception:
            logger.warning("conflict_resolver failed, falling back to heuristic", exc_info=True)

    # Fallback: High similarity — check content overlap
    overlap = _content_overlap(content, best_content)
    if overlap > CONTENT_OVERLAP_RATIO:
        return DedupResult(
            decision=DedupDecision.MERGE,
            existing_block_id=best_id,
            similarity=best_sim,
            reason=f"high_overlap (sim={best_sim:.3f}, overlap={overlap:.2f})",
            block_type=block_type,
        )

    return DedupResult(
        decision=DedupDecision.CREATE,
        reason=f"different_content (sim={best_sim:.3f}, overlap={overlap:.2f})",
        block_type=block_type,
    )
