"""
recall_cache.py — Local file-based fallback cache for the Memvault recall pipeline.

When the Core API is unavailable, recall can serve responses from these cache files
instead of returning nothing. Writes are atomic (temp-file + os.replace). All
functions are fault-tolerant: they never raise — cache is best-effort.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DIR = Path.home() / "Claude" / "memvault" / "cache"
CASCADE_CACHE_DIR = CACHE_DIR / "cascade"
ATTITUDE_CACHE_DIR = CACHE_DIR / "attitudes"

CASCADE_TTL = 1800          # 30 minutes — fresh window
CASCADE_STALE_TTL = 14400   # 4 hours   — stale-but-usable fallback
ATTITUDE_TTL = 900          # 15 minutes
ATTITUDE_STALE_TTL = 3600   # 1 hour

MAX_CACHE_ENTRIES = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_query(query: str) -> str:
    """Lowercase, strip, collapse internal whitespace to a single space."""
    return " ".join(query.lower().split())


def query_hash(normalized: str) -> str:
    """Return the first 16 hex chars of the SHA-256 digest of *normalized*."""
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(timestamp_str: str) -> float:
    """Return elapsed seconds since *timestamp_str* (ISO 8601 with tz)."""
    ts = datetime.fromisoformat(timestamp_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (_now_utc() - ts).total_seconds()


# ---------------------------------------------------------------------------
# Core cache I/O
# ---------------------------------------------------------------------------

def read_cache(
    cache_dir: Path,
    q_hash: str,
    ttl: int,
    stale_ttl: int,
) -> tuple[dict | None, bool]:
    """
    Read a cached response by hash.

    Returns
    -------
    (response, is_stale)
        - fresh hit  → (response, False)
        - stale hit  → (response, True)
        - miss/error → (None, False)
    """
    path = cache_dir / f"{q_hash}.json"
    if not path.exists():
        return None, False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = _age_seconds(data["timestamp"])

        if age <= ttl:
            return data["response"], False
        if age <= stale_ttl:
            return data["response"], True
        return None, False

    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        # Corrupt or unreadable — remove silently
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None, False


def write_cache(
    cache_dir: Path,
    q_hash: str,
    query: str,
    normalized: str,
    response: dict,
    ttl: int,
) -> None:
    """
    Atomically write *response* to the cache.

    Also updates ``_latest.json`` and evicts LRU entries when the cache
    exceeds MAX_CACHE_ENTRIES.  Never raises.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)

        entry = {
            "query": query,
            "query_normalized": normalized,
            "timestamp": _now_utc().isoformat(),
            "ttl_seconds": ttl,
            "response": response,
        }
        payload = json.dumps(entry, ensure_ascii=False)

        # Primary entry — atomic write
        path = cache_dir / f"{q_hash}.json"
        tmp = cache_dir / f"{q_hash}.{os.getpid()}.tmp"
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

        # _latest.json — atomic write
        latest = cache_dir / "_latest.json"
        tmp_l = cache_dir / f"_latest.{os.getpid()}.tmp"
        tmp_l.write_text(payload, encoding="utf-8")
        os.replace(tmp_l, latest)

        evict_lru(cache_dir, MAX_CACHE_ENTRIES)

    except Exception:  # noqa: BLE001
        pass


def read_latest(
    cache_dir: Path,
    stale_ttl: int,
) -> tuple[dict | None, bool]:
    """
    Return the most-recently-written cached response regardless of query.

    This is always a fallback, so there is no fresh-TTL check — only the
    stale_ttl window is evaluated.

    Returns
    -------
    (response, True)  — if within stale_ttl
    (None, False)     — otherwise
    """
    path = cache_dir / "_latest.json"
    if not path.exists():
        return None, False

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = _age_seconds(data["timestamp"])

        if age <= stale_ttl:
            return data["response"], True
        return None, False

    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None, False


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------

def evict_lru(cache_dir: Path, max_entries: int) -> None:
    """
    Delete oldest .json files (by mtime) until at most *max_entries* remain.

    ``_latest.json`` is excluded from counting and from deletion.
    """
    try:
        entries = [
            p for p in cache_dir.glob("*.json")
            if p.name != "_latest.json"
        ]
        if len(entries) <= max_entries:
            return

        entries.sort(key=lambda p: p.stat().st_mtime)
        for path in entries[: len(entries) - max_entries]:
            path.unlink(missing_ok=True)

    except OSError:
        pass
