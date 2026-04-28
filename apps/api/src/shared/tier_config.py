"""Four-tier data lifecycle configuration — Hot / Warm / Cold / Frozen.

Centralized tier thresholds per module. Used by:
  - archive_cold_data.py (lifecycle transitions)
  - search services (query routing across tiers)
  - models (frozen table definitions)

Tier definitions:
  Hot    — Full indexes (HNSW + GIN + B-tree), fastest search
  Warm   — Main table, no HNSW (embedding deleted), GIN + B-tree still active
  Cold   — Archive table, B-tree + GIN only, content may be S3 ref
  Frozen — Frozen table (minimal metadata) + S3 (full content), legal retention
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TierThreshold:
    """Age thresholds (in days) for tier boundaries.

    Data lifecycle: Hot → Warm → Cold → Frozen
      - age ≤ hot_days        → Hot
      - hot_days < age ≤ warm_days  → Warm
      - warm_days < age ≤ cold_days → Cold
      - age > cold_days       → Frozen
    """

    hot_days: int
    warm_days: int
    cold_days: int
    frozen_retention_years: int = 5  # minimum legal retention


# Per-module tier configuration
TIER_THRESHOLDS: dict[str, TierThreshold] = {
    # Memory blocks decay fast — aligned with Mem0's 7-30d session window
    "memvault": TierThreshold(hot_days=14, warm_days=180, cold_days=1095, frozen_retention_years=5),
    # Intelligence reports have long-term reference value
    "intelflow": TierThreshold(
        hot_days=180, warm_days=730, cold_days=1825, frozen_retention_years=5,
    ),
    # Financial records — Taiwan tax audit period is 5 years
    "finance": TierThreshold(hot_days=90, warm_days=365, cold_days=1825, frozen_retention_years=5),
    # Tasks cool down quickly after completion
    "taskflow": TierThreshold(hot_days=30, warm_days=365, cold_days=1825, frozen_retention_years=5),
    # Knowledge graphs retain value longer
    "ideagraph": TierThreshold(
        hot_days=90, warm_days=730, cold_days=1825, frozen_retention_years=5,
    ),
}

# S3 bucket names
S3_ARCHIVE_BUCKET = "workshop-archive"  # Cold-Blob storage
S3_FROZEN_BUCKET = "workshop-frozen"  # Frozen tier storage

# Blob threshold — content larger than this goes to S3 instead of PG
BLOB_THRESHOLD_BYTES = 10240  # 10 KB

# Batch size for lifecycle transitions (commit per batch)
LIFECYCLE_BATCH_SIZE = 500


def get_tier(module: str, age_days: int) -> str:
    """Determine the tier for a data item based on its age.

    Args:
        module: Module name (memvault, intelflow, etc.)
        age_days: Age of the data item in days

    Returns:
        Tier name: "hot", "warm", "cold", or "frozen"
    """
    t = TIER_THRESHOLDS.get(module)
    if t is None:
        # Unknown module — default to conservative thresholds
        t = TierThreshold(hot_days=90, warm_days=365, cold_days=1825)

    if age_days <= t.hot_days:
        return "hot"
    elif age_days <= t.warm_days:
        return "warm"
    elif age_days <= t.cold_days:
        return "cold"
    else:
        return "frozen"


def get_threshold(module: str) -> TierThreshold:
    """Get the tier threshold config for a module."""
    return TIER_THRESHOLDS.get(
        module,
        TierThreshold(hot_days=90, warm_days=365, cold_days=1825),
    )


def get_frozen_retention_cutoff(module: str) -> int:
    """Get the frozen retention cutoff in days.

    Returns the number of days after which frozen data
    MAY be purged (requires explicit approval).
    """
    t = get_threshold(module)
    return t.cold_days + (t.frozen_retention_years * 365)


def get_retention_summary() -> dict[str, dict]:
    """Generate a retention summary for all modules.

    Returns per-module tier boundary and retention info.
    Used by audit tools and lifecycle reporting.
    """
    summary = {}
    for module, t in TIER_THRESHOLDS.items():
        total_retention_days = (
            t.cold_days + t.frozen_retention_years * 365
        )
        summary[module] = {
            "hot_days": t.hot_days,
            "warm_days": t.warm_days,
            "cold_days": t.cold_days,
            "frozen_retention_years": t.frozen_retention_years,
            "total_retention_days": total_retention_days,
            "total_retention_years": round(
                total_retention_days / 365, 1,
            ),
        }
    return summary
