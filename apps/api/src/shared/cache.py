"""Service result cache — Redis-backed caching for service layer methods.

Provides:
- @cached decorator for service methods
- register_invalidation() to wire EventBus events to cache clearing
- Low-level cache_get/cache_set/cache_delete_pattern helpers

All operations silently degrade when Redis is unavailable.
Key format: cache:{module}:{operation}:{space_id}[:{params_hash}]
"""

import functools
import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from .redis import get_redis

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # 1 hour


# ======================== Low-level ops ========================


async def cache_get(key: str) -> Any | None:
    """Get a cached value. Returns None on miss or error."""
    try:
        r = get_redis()
        data = await r.get(key)
        if data is not None:
            return json.loads(data)
    except Exception:
        logger.debug("cache_get failed for %s", key, exc_info=True)
    return None


async def cache_set(key: str, value: Any, ttl: int = _DEFAULT_TTL) -> None:
    """Set a cached value with TTL."""
    try:
        r = get_redis()
        await r.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        logger.debug("cache_set failed for %s", key, exc_info=True)


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern. Returns count deleted."""
    try:
        r = get_redis()
        deleted = 0
        batch: list[str] = []
        async for key in r.scan_iter(match=pattern, count=100):
            batch.append(key)
            if len(batch) >= 100:
                await r.unlink(*batch)
                deleted += len(batch)
                batch = []
        if batch:
            await r.unlink(*batch)
            deleted += len(batch)
        return deleted
    except Exception:
        logger.debug("cache_delete_pattern failed for %s", pattern, exc_info=True)
        return 0


# ======================== @cached decorator ========================


def _build_cache_key(module: str, operation: str, kwargs: dict, key_params: tuple[str, ...]) -> str:
    """Build a deterministic cache key from parameters."""
    parts = [f"cache:{module}:{operation}"]
    for param in key_params:
        val = kwargs.get(param, "")
        parts.append(str(val))
    # Hash remaining kwargs (excluding db, self, key_params already used)
    extra = {k: v for k, v in kwargs.items() if k not in ("db", "self") and k not in key_params}
    if extra:
        extra_hash = hashlib.md5(  # noqa: S324
            json.dumps(extra, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        parts.append(extra_hash)
    return ":".join(parts)


def cached(
    module: str,
    operation: str,
    ttl: int = _DEFAULT_TTL,
    key_params: tuple[str, ...] = ("space_id",),
) -> Callable:
    """Decorator to cache async service method results in Redis.

    Usage:
        @cached("finance", "list_categories", ttl=3600, key_params=("space_id",))
        async def list_tree(self, db, space_id, user_id=None):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Build kwargs dict from positional args using function signature
            import inspect

            sig = inspect.signature(fn)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            all_kwargs = dict(bound.arguments)

            cache_key = _build_cache_key(module, operation, all_kwargs, key_params)

            # Try cache
            hit = await cache_get(cache_key)
            if hit is not None:
                return hit

            # Call original
            result = await fn(*args, **kwargs)

            # Store in cache — convert Pydantic models to dicts
            cacheable = _to_cacheable(result)
            if cacheable is not None:
                await cache_set(cache_key, cacheable, ttl)

            return result

        wrapper._cache_module = module  # type: ignore[attr-defined]
        wrapper._cache_operation = operation  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _to_cacheable(obj: Any) -> Any:
    """Convert result to JSON-serializable form for caching."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_to_cacheable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_cacheable(v) for k, v in obj.items()}
    return None


# ======================== EventBus invalidation ========================


def register_invalidation(
    module: str,
    operations: list[str],
    events: list[str],
) -> None:
    """Wire EventBus events to cache invalidation.

    When any listed event fires, all cache keys matching
    cache:{module}:{operation}:{space_id}:* are deleted.
    """
    from src.events_stub.bus import Event, event_bus

    async def _invalidate(event: Event) -> None:
        space_id = event.data.get("space_id", "*")
        for op in operations:
            pattern = f"cache:{module}:{op}:{space_id}:*"
            deleted = await cache_delete_pattern(pattern)
            if deleted:
                logger.debug(
                    "cache invalidated: %s:%s space=%s (%d keys)",
                    module,
                    op,
                    space_id,
                    deleted,
                )

    for event_type in events:
        event_bus.channel(event_type).subscribe_handler(_invalidate)
