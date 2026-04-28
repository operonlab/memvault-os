"""Dynamic timeout calculation.

Usage:
    from sdk_client.timeout import dynamic_timeout

    # Simple: base + factor * context
    t = dynamic_timeout(base=30, factor=2.0, context=file_size_mb, cap=180)

    # Presets
    t = timeout_for_network()       # 5-15s
    t = timeout_for_api()           # 15-60s
    t = timeout_for_subprocess()    # 30-300s
    t = timeout_for_llm(tokens=2000) # scaled by token count
"""

from __future__ import annotations


def dynamic_timeout(
    base: float,
    factor: float = 0.0,
    context: float = 0.0,
    cap: float = 300.0,
    floor: float | None = None,
) -> float:
    """Calculate timeout: clamp(floor, base + factor * context, cap).

    Args:
        base: Base timeout in seconds.
        factor: Multiplier for context variable.
        context: Context variable (file size, token count, complexity, etc.).
        cap: Maximum timeout (upper bound).
        floor: Minimum timeout (lower bound, defaults to base).
    """
    if floor is None:
        floor = base
    return min(cap, max(floor, base + factor * context))


def timeout_for_network(payload_kb: float = 0) -> float:
    """Network operation: 5-15s, scaled by payload."""
    return dynamic_timeout(base=5, factor=0.01, context=payload_kb, cap=15)


def timeout_for_api(complexity: float = 0) -> float:
    """API call: 15-60s, scaled by complexity."""
    return dynamic_timeout(base=15, factor=5.0, context=complexity, cap=60)


def timeout_for_subprocess(expected_seconds: float = 0) -> float:
    """Subprocess: 30-300s, scaled by expected duration."""
    return dynamic_timeout(base=30, factor=1.5, context=expected_seconds, cap=300)


def timeout_for_llm(tokens: int = 0, model_speed: float = 1.0) -> float:
    """LLM inference: scaled by token count and model speed.

    Args:
        tokens: Approximate output tokens expected.
        model_speed: Multiplier (1.0 = normal, 2.0 = slow model).
    """
    base = 15 * model_speed
    # ~100 tokens/sec for fast models, scale up for more tokens
    factor = 0.015 * model_speed
    return dynamic_timeout(base=base, factor=factor, context=tokens, cap=180)
