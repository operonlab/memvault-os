"""Dynamic tier promotion/demotion manager for memvault memory blocks.

Cannibalized from memory-lancedb-pro's three-tier TierManager (G7),
adapted to Workshop's four-tier lifecycle: Hot / Warm / Cold / Frozen.

Promotion paths:
  Frozen → Cold → Warm → Hot  (based on access count, composite score, importance)

Demotion paths:
  Hot → Warm → Cold → Frozen  (based on composite decay, age, access count)

Design notes:
  - Evaluation-only: returns transitions but never writes to DB (caller's job)
  - Uses dataclasses to match scoring_pipeline.py style (no Pydantic)
  - `composite` score is the caller-computed Weibull-decayed score from
    scoring_pipeline.weibull_decay(), range [0.0, 1.0]
  - `importance` maps to MemoryBlock.confidence (caller normalises)
  - Frozen tier is rarely promoted back to Cold (exceptional "thaw" path)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TierTransition
# ---------------------------------------------------------------------------


@dataclass
class TierTransition:
    """Describes a single recommended tier change for one memory block."""

    memory_id: str
    from_tier: str  # "hot" | "warm" | "cold" | "frozen"
    to_tier: str  # "hot" | "warm" | "cold" | "frozen"
    reason: str


# ---------------------------------------------------------------------------
# TierThresholds
# ---------------------------------------------------------------------------


@dataclass
class TierThresholds:
    """Configurable thresholds for tier promotion / demotion decisions.

    Promotion (upward) — from lower to higher activity tier:
      Warm → Hot  : access_count >= hot_promo_access AND composite >= hot_promo_composite
                    AND importance >= hot_promo_importance
      Cold → Warm : access_count >= warm_promo_access AND composite >= warm_promo_composite
      Frozen → Cold: explicit thaw only (frozen_thaw_composite + frozen_thaw_access)

    Demotion (downward) — from higher to lower activity tier:
      Hot → Warm  : composite < warm_demo_composite
                    OR (age_days > warm_demo_age_days AND access_count < warm_demo_access)
      Warm → Cold : composite < cold_demo_composite
                    OR (age_days > cold_demo_age_days AND access_count < cold_demo_access)
      Cold → Frozen: composite < frozen_demo_composite AND age_days > frozen_demo_age_days
    """

    # --- Hot promotion thresholds (from Warm → Hot) ---
    hot_promo_access: int = 10
    hot_promo_composite: float = 0.7
    hot_promo_importance: float = 0.8

    # --- Warm promotion thresholds (from Cold → Warm) ---
    warm_promo_access: int = 3
    warm_promo_composite: float = 0.4

    # --- Frozen thaw thresholds (from Frozen → Cold, exceptional path) ---
    frozen_thaw_composite: float = 0.35
    frozen_thaw_access: int = 5

    # --- Hot demotion thresholds (Hot → Warm) ---
    warm_demo_composite: float = 0.15
    warm_demo_age_days: int = 60
    warm_demo_access: int = 3

    # --- Warm demotion thresholds (Warm → Cold) ---
    cold_demo_composite: float = 0.08
    cold_demo_age_days: int = 180
    cold_demo_access: int = 1

    # --- Cold demotion thresholds (Cold → Frozen) ---
    frozen_demo_composite: float = 0.05
    frozen_demo_age_days: int = 365


# Module-default thresholds — callers may override
DEFAULT_THRESHOLDS = TierThresholds()


# ---------------------------------------------------------------------------
# TierableMemory
# ---------------------------------------------------------------------------


@dataclass
class TierableMemory:
    """Minimal memory fields required for tier evaluation.

    Caller extracts these from MemoryBlock ORM objects so this module
    stays free of SQLAlchemy imports.
    """

    memory_id: str
    current_tier: str  # "hot" | "warm" | "cold" | "frozen"
    importance: float  # maps to MemoryBlock.confidence, range [0, 1]
    access_count: int  # MemoryBlock.access_count
    created_at: datetime  # MemoryBlock.created_at (tz-aware)


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------


def _age_days(memory: TierableMemory, now: datetime) -> float:
    """Compute age in fractional days from created_at to now."""
    delta = now - memory.created_at
    return max(delta.total_seconds() / 86_400.0, 0.0)


def evaluate_tier(
    memory: TierableMemory,
    decay_score: float,
    thresholds: TierThresholds | None = None,
    now: datetime | None = None,
) -> TierTransition | None:
    """Evaluate a single memory block for tier change.

    Args:
        memory: Memory metadata needed for evaluation.
        decay_score: Composite Weibull-decayed score from scoring_pipeline,
                     range [0.0, 1.0]. Higher = more relevant / less decayed.
        thresholds: Override default thresholds (pass None to use defaults).
        now: Reference time for age computation; defaults to UTC now.

    Returns:
        TierTransition if a tier change is recommended, None otherwise.
    """
    t = thresholds or DEFAULT_THRESHOLDS
    now = now or datetime.now(UTC)
    age = _age_days(memory, now)
    tier = memory.current_tier
    acc = memory.access_count
    imp = memory.importance
    cs = decay_score

    if tier == "frozen":
        # Frozen → Cold: exceptional thaw path
        if cs >= t.frozen_thaw_composite and acc >= t.frozen_thaw_access:
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="frozen",
                to_tier="cold",
                reason=(
                    f"Thaw: composite ({cs:.3f}) >= {t.frozen_thaw_composite} "
                    f"and access ({acc}) >= {t.frozen_thaw_access}"
                ),
            )

    elif tier == "cold":
        # Cold → Warm: promotion
        if acc >= t.warm_promo_access and cs >= t.warm_promo_composite:
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="cold",
                to_tier="warm",
                reason=(
                    f"Promote: access ({acc}) >= {t.warm_promo_access} "
                    f"and composite ({cs:.3f}) >= {t.warm_promo_composite}"
                ),
            )
        # Cold → Frozen: demotion
        if cs < t.frozen_demo_composite and age > t.frozen_demo_age_days:
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="cold",
                to_tier="frozen",
                reason=(
                    f"Freeze: composite ({cs:.3f}) < {t.frozen_demo_composite} "
                    f"and age ({age:.0f}d) > {t.frozen_demo_age_days}d"
                ),
            )

    elif tier == "warm":
        # Warm → Hot: promotion (requires high importance as well)
        if (
            acc >= t.hot_promo_access
            and cs >= t.hot_promo_composite
            and imp >= t.hot_promo_importance
        ):
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="warm",
                to_tier="hot",
                reason=(
                    f"Promote: access ({acc}) >= {t.hot_promo_access}, "
                    f"composite ({cs:.3f}) >= {t.hot_promo_composite}, "
                    f"importance ({imp:.3f}) >= {t.hot_promo_importance}"
                ),
            )
        # Warm → Cold: demotion
        if cs < t.cold_demo_composite or (age > t.cold_demo_age_days and acc < t.cold_demo_access):
            reason_parts = []
            if cs < t.cold_demo_composite:
                reason_parts.append(f"composite ({cs:.3f}) < {t.cold_demo_composite}")
            if age > t.cold_demo_age_days and acc < t.cold_demo_access:
                reason_parts.append(
                    f"age ({age:.0f}d) > {t.cold_demo_age_days}d "
                    f"with low access ({acc} < {t.cold_demo_access})"
                )
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="warm",
                to_tier="cold",
                reason="Demote: " + "; ".join(reason_parts),
            )

    elif tier == "hot":
        # Hot → Warm: demotion
        if cs < t.warm_demo_composite or (age > t.warm_demo_age_days and acc < t.warm_demo_access):
            reason_parts = []
            if cs < t.warm_demo_composite:
                reason_parts.append(f"composite ({cs:.3f}) < {t.warm_demo_composite}")
            if age > t.warm_demo_age_days and acc < t.warm_demo_access:
                reason_parts.append(
                    f"age ({age:.0f}d) > {t.warm_demo_age_days}d "
                    f"with low access ({acc} < {t.warm_demo_access})"
                )
            return TierTransition(
                memory_id=memory.memory_id,
                from_tier="hot",
                to_tier="warm",
                reason="Demote: " + "; ".join(reason_parts),
            )

    else:
        logger.warning("evaluate_tier: unknown tier %r for memory %s", tier, memory.memory_id)

    return None


def evaluate_all(
    memories: list[TierableMemory],
    decay_scores: dict[str, float],
    thresholds: TierThresholds | None = None,
    now: datetime | None = None,
) -> list[TierTransition]:
    """Evaluate a batch of memory blocks and return all recommended transitions.

    Args:
        memories: List of TierableMemory objects to evaluate.
        decay_scores: Mapping of memory_id → composite Weibull-decayed score.
                      Entries with no matching score are silently skipped.
        thresholds: Override default thresholds (pass None to use defaults).
        now: Reference time; defaults to UTC now. Passed unchanged to each
             evaluate_tier() call for consistent batch evaluation.

    Returns:
        List of TierTransition objects (one per memory that needs a change).
        Order matches the input memories list.
    """
    now = now or datetime.now(UTC)
    transitions: list[TierTransition] = []

    for memory in memories:
        score = decay_scores.get(memory.memory_id)
        if score is None:
            continue
        transition = evaluate_tier(memory, score, thresholds=thresholds, now=now)
        if transition is not None:
            transitions.append(transition)

    return transitions
