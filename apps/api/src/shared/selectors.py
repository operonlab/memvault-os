"""NgRx-style memoized Selectors.

Cannibalized from pystorex/store_selectors.py — adapted to Workshop:
- No immutables.Map (plain dict state)
- Simpler cache (identity-based, not deep equality by default)
- TTL support for time-sensitive projections

Usage:
    from src.shared.selectors import create_selector

    select_wallets = create_selector(lambda s: s["wallets"])
    select_total = create_selector(
        select_wallets,
        result_fn=lambda wallets: sum(w["balance"] for w in wallets.values()),
    )

    total = select_total(store.get_state())  # memoized
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def create_selector(
    *input_selectors: Callable[[Any], Any],
    result_fn: Callable[..., Any] | None = None,
    ttl: float | None = None,
    maxsize: int = 128,
) -> Callable[[Any], Any]:
    """Create a memoized composite selector.

    Args:
        *input_selectors: Functions that extract slices from state.
        result_fn: Combines the slices into a derived value.
        ttl: Cache TTL in seconds (None = forever).
        maxsize: Max cache entries.

    Returns:
        A memoized selector function with .cache_info() and .cache_clear().
    """
    if not result_fn and len(input_selectors) == 1:
        return input_selectors[0]

    if not result_fn:
        result_fn = lambda *args: args  # noqa: E731

    # Cache: list of (timestamp, input_ids, result)
    cache: list[tuple[float, tuple, Any]] = []
    last_result: Any = None
    hits = 0
    misses = 0

    def selector(state: Any) -> Any:
        nonlocal cache, last_result, hits, misses

        # Extract inputs
        inputs = tuple(sel(state) for sel in input_selectors)
        input_ids = tuple(id(v) for v in inputs)

        now = time.monotonic()

        # Evict expired entries
        if ttl is not None:
            cache = [(t, ids, r) for t, ids, r in cache if now - t <= ttl]

        # Trim to maxsize
        while len(cache) >= maxsize:
            cache.pop(0)

        # Check cache (identity comparison — fast, no deep copy)
        for _, cached_ids, cached_result in cache:
            if cached_ids == input_ids:
                hits += 1
                return cached_result

        # Cache miss — compute
        misses += 1
        result = result_fn(*inputs)
        cache.append((now, input_ids, result))
        last_result = result
        return result

    def cache_info() -> dict:
        return {"hits": hits, "misses": misses, "maxsize": maxsize, "currsize": len(cache)}

    def cache_clear() -> None:
        nonlocal cache, hits, misses
        cache.clear()
        hits = 0
        misses = 0

    selector.cache_info = cache_info  # type: ignore[attr-defined]
    selector.cache_clear = cache_clear  # type: ignore[attr-defined]

    return selector
