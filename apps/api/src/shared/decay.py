"""Weibull memory decay functions — tier-aware forgetting curves.

Extracted from memvault scoring_pipeline for cross-module reuse.
S(t) = floor + (1 - floor) * exp(-(t/λ)^β)

Tier parameters:
  core: slow decay (important long-term memories)
  hot:  standard exponential decay
  warm: accelerated decay
  cold: rapid decay for rarely-accessed content
"""

import math

WEIBULL_PARAMS: dict[str, dict[str, float]] = {
    "core": {"beta": 0.8, "lambda_": 180.0, "floor": 0.4},
    "hot": {"beta": 1.0, "lambda_": 60.0, "floor": 0.3},
    "warm": {"beta": 1.2, "lambda_": 30.0, "floor": 0.2},
    "cold": {"beta": 1.5, "lambda_": 14.0, "floor": 0.1},
}


def weibull_decay(age_days: float, tier: str = "hot") -> float:
    """Compute Weibull survival function for memory decay.

    S(t) = floor + (1 - floor) * exp(-(t/λ)^β)
    """
    params = WEIBULL_PARAMS.get(tier, WEIBULL_PARAMS["hot"])
    beta = params["beta"]
    lambda_ = params["lambda_"]
    floor = params["floor"]

    if age_days <= 0:
        return 1.0

    survival = math.exp(-((age_days / lambda_) ** beta))
    return floor + (1 - floor) * survival


def weibull_decay_with_half_life(
    age_days: float, effective_half_life: float, tier: str = "hot"
) -> float:
    """Weibull decay with an access-adjusted characteristic lifetime.

    Replaces the tier's default lambda_ with effective_half_life so that
    frequently-accessed memories decay more slowly.
    """
    params = WEIBULL_PARAMS.get(tier, WEIBULL_PARAMS["hot"])
    beta = params["beta"]
    floor = params["floor"]

    if age_days <= 0:
        return 1.0

    survival = math.exp(-((age_days / effective_half_life) ** beta))
    return floor + (1 - floor) * survival
