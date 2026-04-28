"""Access Tracker — reinforcement-based memory decay.

Cannibalized from memory-lancedb-pro (G6).

Tracks access_count + last_accessed_at per MemoryBlock and computes an
effective half-life so frequently-accessed memories decay more slowly.

Key functions:
  record_access(memory_id, db)  — increment access counter on MemoryBlock
  compute_effective_half_life(...)  — effective half-life with access reinforcement
"""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Access count itself decays with a 30-day half-life (mirrors source pattern)
_ACCESS_DECAY_HALF_LIFE_DAYS: float = 30.0
# Hard cap: effective half-life <= base * MAX_MULTIPLIER
_MAX_MULTIPLIER: float = 10.0
# Reinforcement scaling factor (0 = disabled, 1 = full)
_REINFORCEMENT_FACTOR: float = 1.0


@dataclass
class AccessRecord:
    """Snapshot of access tracking state for a single memory block."""

    memory_id: str
    access_count: int
    last_accessed_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Core computation (pure, no I/O)
# ---------------------------------------------------------------------------


def compute_effective_half_life(
    access_count: int,
    last_accessed_at: datetime | None,
    created_at: datetime,
    base_half_life_days: float = 30.0,
    reinforcement_factor: float = _REINFORCEMENT_FACTOR,
    max_multiplier: float = _MAX_MULTIPLIER,
) -> float:
    """Compute effective half-life in days, extended by access reinforcement.

    Algorithm (port of memory-lancedb-pro access-tracker.ts):
      1. Compute access freshness: exponential decay on time since last access
         (30-day half-life for the access recency itself).
      2. Effective access count = raw_count * freshness_factor.
      3. Extension = base * reinforcement_factor * log1p(effective_count).
      4. Result capped at base * max_multiplier.

    Args:
        access_count: Raw number of times the block was retrieved.
        last_accessed_at: Timestamp of most recent access (None = never).
        created_at: Block creation timestamp (unused in formula, kept for
            future cohort-based tuning).
        base_half_life_days: Starting half-life before reinforcement.
        reinforcement_factor: Scaling multiplier (0 disables reinforcement).
        max_multiplier: Hard cap expressed as a multiple of base_half_life_days.

    Returns:
        Effective half-life in days (>= base_half_life_days).
    """
    if reinforcement_factor == 0 or access_count <= 0 or last_accessed_at is None:
        return base_half_life_days

    now = datetime.now(UTC)
    # Normalise to UTC-aware for safe subtraction
    laa = last_accessed_at if last_accessed_at.tzinfo else last_accessed_at.replace(tzinfo=UTC)
    days_since = max(0.0, (now - laa).total_seconds() / 86400.0)

    # Access freshness decays exponentially (30-day half-life)
    access_freshness = math.exp(-days_since * (math.log(2) / _ACCESS_DECAY_HALF_LIFE_DAYS))

    effective_count = access_count * access_freshness

    # Logarithmic extension — diminishing returns
    extension = base_half_life_days * reinforcement_factor * math.log1p(effective_count)
    result = base_half_life_days + extension

    cap = base_half_life_days * max_multiplier
    return min(result, cap)


# ---------------------------------------------------------------------------
# DB write-back
# ---------------------------------------------------------------------------


async def record_access(memory_id: str, db: AsyncSession) -> None:
    """Increment access_count and update last_accessed_at on a MemoryBlock.

    Safe to call fire-and-forget — logs warnings on failure, never raises.
    Import is deferred to avoid a circular import with memvault.models.
    """
    try:
        # Deferred import avoids circular dependency at module load time
        from src.memvault.models import MemoryBlock

        now = datetime.now(UTC)
        stmt = (
            update(MemoryBlock)
            .where(MemoryBlock.id == memory_id)
            .values(
                access_count=MemoryBlock.access_count + 1,
                last_accessed_at=now,
            )
        )
        await db.execute(stmt)
        await db.commit()
    except Exception:
        logger.warning(
            "access_tracker: failed to record access for %s", memory_id[:8], exc_info=True
        )


async def get_access_record(memory_id: str, db: AsyncSession) -> AccessRecord | None:
    """Fetch access tracking state for a single MemoryBlock.

    Returns None if the block does not exist.
    """
    from src.memvault.models import MemoryBlock

    result = await db.execute(
        select(
            MemoryBlock.id,
            MemoryBlock.access_count,
            MemoryBlock.last_accessed_at,
            MemoryBlock.created_at,
        ).where(MemoryBlock.id == memory_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return AccessRecord(
        memory_id=row.id,
        access_count=row.access_count,
        last_accessed_at=row.last_accessed_at,
        created_at=row.created_at,
    )
