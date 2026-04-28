"""Knowledge Scale Detection — adaptive strategy based on knowledge volume.

Detects the scale of a space's knowledge base and recommends the optimal
retrieval strategy. Scale is cached in Redis (1h TTL) to avoid per-query overhead.

Scale tiers:
  MICRO  (< 200K chars): Full context prompting — no RAG overhead needed.
  SMALL  (200K-2M):      Compiled wiki — pre-generated summaries as primary.
  MEDIUM (2M-50M):       Cascade recall — current default behavior.
  LARGE  (> 50M):        Full stack with agent routing hints.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Scale boundaries in characters
_MICRO_MAX = 200_000
_SMALL_MAX = 2_000_000
_MEDIUM_MAX = 50_000_000

_CACHE_TTL = 3600  # 1 hour


class KnowledgeScale(StrEnum):
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


async def detect_scale(db: AsyncSession, space_id: str) -> KnowledgeScale:
    """Detect knowledge scale for a space. Cached in Redis for 1 hour."""
    # Check Redis cache first
    cache_key = f"memvault:scale:{space_id}"
    try:
        from src.shared.redis import get_redis

        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return KnowledgeScale(cached.decode())
    except Exception:
        pass

    # Query DB for total content size
    from .models import MemoryBlock

    result = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(func.length(MemoryBlock.content)), 0)).where(
                MemoryBlock.space_id == space_id,
                MemoryBlock.deleted_at.is_(None),
                MemoryBlock.invalid_at.is_(None),
            )
        )
    ).one()

    block_count, total_chars = int(result[0]), int(result[1])

    if total_chars < _MICRO_MAX:
        scale = KnowledgeScale.MICRO
    elif total_chars < _SMALL_MAX:
        scale = KnowledgeScale.SMALL
    elif total_chars < _MEDIUM_MAX:
        scale = KnowledgeScale.MEDIUM
    else:
        scale = KnowledgeScale.LARGE

    logger.info(
        "detect_scale: space=%s blocks=%d chars=%d scale=%s",
        space_id, block_count, total_chars, scale.value,
    )

    # Cache in Redis
    try:
        await redis.setex(cache_key, _CACHE_TTL, scale.value)
    except Exception:
        pass

    return scale
