"""Speculative prefetch cache — cross-module foundation for predictive prefetch.

Completely separate from stable @cached in cache.py.
Key prefix: prefetch:* (never collides with cache:*)
All operations silently degrade when Redis is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .redis import get_redis

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 300  # 5 minutes
_METRICS_TTL = 604800  # 7 days
_INFLIGHT_TTL = 5  # seconds


@dataclass
class PrefetchFingerprint:
    """Module-agnostic cache key fingerprint.

    Each module puts its own fields (e.g. memvault puts consumer/task_mode/intent/tags/top_k/scope;
    capture puts module/entity_type/adapter_name).
    """

    module: str
    space_id: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def cache_key(self) -> str:
        fields_hash = hashlib.sha256(
            json.dumps(self.fields, sort_keys=True).encode()
        ).hexdigest()[:12]
        return f"prefetch:{self.module}:{self.space_id}:{fields_hash}"

    @property
    def inflight_key(self) -> str:
        return f"prefetch_inflight:{self.module}:{self.space_id}:{self.cache_key.split(':')[-1]}"


@dataclass
class PrefetchMetrics:
    """Prefetch performance metrics, derived from Redis hash counters."""

    query_count: int = 0
    prefetch_count: int = 0
    hit_count: int = 0
    miss_count: int = 0
    waste_count: int = 0
    skip_count: int = 0
    latency_saved_ms: float = 0.0
    compute_cost_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        return self.hit_count / self.prefetch_count if self.prefetch_count > 0 else 0.0

    @property
    def waste_rate(self) -> float:
        return self.waste_count / self.prefetch_count if self.prefetch_count > 0 else 0.0

    @property
    def avg_latency_saved_ms(self) -> float:
        return self.latency_saved_ms / self.hit_count if self.hit_count > 0 else 0.0


class SpeculativePrefetchCache:
    """Redis-backed speculative cache, separate from stable @cached.

    Key schema:
        prefetch:{module}:{space_id}:{fields_hash}         -> cards JSON, TTL 300s
        prefetch_inflight:{module}:{space_id}:{hash}        -> SETNX lock, TTL 5s
        prefetch_metrics:{module}:{space_id}:{date}         -> Hash, TTL 7d
    """

    def __init__(self, module: str, default_ttl: int = _DEFAULT_TTL) -> None:
        self._module = module
        self._default_ttl = default_ttl

    def _metrics_key(self, space_id: str) -> str:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"prefetch_metrics:{self._module}:{space_id}:{date_str}"

    # ── Cache operations ──

    async def get(self, fp: PrefetchFingerprint) -> list[dict] | None:
        """Get cached prefetch result. Returns None on miss or error."""
        try:
            r = get_redis()
            data = await r.get(fp.cache_key)
            if data is not None:
                return json.loads(data)
        except Exception:
            logger.debug("prefetch.get failed for %s", fp.cache_key, exc_info=True)
        return None

    async def set(self, fp: PrefetchFingerprint, cards: list[dict], ttl: int | None = None) -> None:
        """Write prefetch result to speculative cache."""
        try:
            r = get_redis()
            await r.set(
                fp.cache_key,
                json.dumps(cards, default=str),
                ex=ttl or self._default_ttl,
            )
        except Exception:
            logger.debug("prefetch.set failed for %s", fp.cache_key, exc_info=True)

    async def delete(self, fp: PrefetchFingerprint) -> None:
        """Delete a specific prefetch entry."""
        try:
            r = get_redis()
            await r.delete(fp.cache_key)
        except Exception:
            logger.debug("prefetch.delete failed for %s", fp.cache_key, exc_info=True)

    # ── In-flight lock (SETNX, prevents duplicate prefetch within 5s) ──

    async def try_acquire_inflight(self, fp: PrefetchFingerprint) -> bool:
        """Try to acquire in-flight lock. Returns True if acquired.

        On Redis failure, returns True (fail-open) to allow prefetch to proceed
        rather than permanently blocking all prefetch during outages.
        """
        try:
            r = get_redis()
            return bool(await r.set(fp.inflight_key, "1", ex=_INFLIGHT_TTL, nx=True))
        except Exception:
            logger.debug("prefetch.inflight failed for %s", fp.inflight_key, exc_info=True)
            return True  # fail-open: allow prefetch on Redis failure

    # ── Metrics ──

    async def record_query(self, space_id: str) -> None:
        """Record a query event (Phase A shadow)."""
        await self._hincrby(space_id, "query_count", 1)

    async def record_prefetch(self, space_id: str, compute_cost_ms: float) -> None:
        """Record a prefetch execution."""
        await self._hincrby(space_id, "prefetch_count", 1)
        await self._hincrbyfloat(space_id, "compute_cost_ms", compute_cost_ms)

    async def record_hit(self, space_id: str, latency_saved_ms: float) -> None:
        """Record a cache hit."""
        await self._hincrby(space_id, "hit_count", 1)
        await self._hincrbyfloat(space_id, "latency_saved_ms", latency_saved_ms)

    async def record_miss(self, space_id: str) -> None:
        """Record a cache miss."""
        await self._hincrby(space_id, "miss_count", 1)

    async def record_waste(self, space_id: str) -> None:
        """Record an unused entry eviction."""
        await self._hincrby(space_id, "waste_count", 1)

    async def record_skip(self, space_id: str) -> None:
        """Record an admission skip."""
        await self._hincrby(space_id, "skip_count", 1)

    async def get_metrics(self, space_id: str) -> PrefetchMetrics:
        """Read current metrics for a space."""
        try:
            r = get_redis()
            key = self._metrics_key(space_id)
            data = await r.hgetall(key)
            if not data:
                return PrefetchMetrics()
            return PrefetchMetrics(
                query_count=int(data.get("query_count", 0)),
                prefetch_count=int(data.get("prefetch_count", 0)),
                hit_count=int(data.get("hit_count", 0)),
                miss_count=int(data.get("miss_count", 0)),
                waste_count=int(data.get("waste_count", 0)),
                skip_count=int(data.get("skip_count", 0)),
                latency_saved_ms=float(data.get("latency_saved_ms", 0)),
                compute_cost_ms=float(data.get("compute_cost_ms", 0)),
            )
        except Exception:
            logger.debug("prefetch.get_metrics failed", exc_info=True)
            return PrefetchMetrics()

    # ── Internal helpers ──

    async def _hincrby(self, space_id: str, field: str, amount: int) -> None:
        try:
            r = get_redis()
            key = self._metrics_key(space_id)
            pipe = r.pipeline()
            pipe.hincrby(key, field, amount)
            pipe.expire(key, _METRICS_TTL)
            await pipe.execute()
        except Exception:
            logger.debug("prefetch._hincrby failed for %s.%s", space_id, field, exc_info=True)

    async def _hincrbyfloat(self, space_id: str, field: str, amount: float) -> None:
        try:
            r = get_redis()
            key = self._metrics_key(space_id)
            pipe = r.pipeline()
            pipe.hincrbyfloat(key, field, amount)
            pipe.expire(key, _METRICS_TTL)
            await pipe.execute()
        except Exception:
            logger.debug("prefetch._hincrbyfloat failed for %s.%s", space_id, field, exc_info=True)
