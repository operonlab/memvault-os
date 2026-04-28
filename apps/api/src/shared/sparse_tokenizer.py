"""BM25-style sparse vector tokenizer for Qdrant.

Generates {token_id: weight} sparse vectors using TF-IDF scoring.
Supports Chinese (jieba) and English (whitespace + normalization).

Phase 3 enhancements:
  - Configurable avgdl per service (was hardcoded 200)
  - Optional global IDF from Redis (falls back to per-doc approximation)
"""

import json
import logging
import math
import re
from collections import Counter

from .search_constants import SERVICE_AVGDL as _SERVICE_AVGDL
from .text_utils import CJK_PATTERN as _CJK_PATTERN
from .text_utils import STOPWORDS as _STOPWORDS

logger = logging.getLogger(__name__)

_WORD_PATTERN = re.compile(r"[a-zA-Z0-9_]+")

# Lazy-load jieba to avoid import overhead when not needed
_jieba = None
# Lazy-load Snowball stemmer for English
_stemmer = None


def _get_jieba():
    global _jieba
    if _jieba is None:
        import jieba

        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    return _jieba


def _get_stemmer():
    global _stemmer
    if _stemmer is None:
        import snowballstemmer

        _stemmer = snowballstemmer.stemmer("english")
    return _stemmer


def _stem_english(word: str) -> str:
    """Stem an English word using Snowball stemmer."""
    return _get_stemmer().stemWord(word)

# BM25 parameters
_K1 = 1.5
_B = 0.75

# Global IDF cache (loaded from Redis on first use)
_global_idf: dict[int, float] | None = None
_global_idf_loaded = False


def _has_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def tokenize(text: str) -> list[str]:
    """Tokenize text into normalized, stemmed tokens (Chinese + English).

    English tokens are Snowball-stemmed so 'characters' and 'character'
    produce the same token hash, improving BM25 recall.
    """
    text = text.lower().strip()
    tokens = []

    if _has_cjk(text):
        jieba = _get_jieba()
        for word in jieba.cut(text):
            word = word.strip()
            if word and word not in _STOPWORDS and len(word) > 0:
                tokens.append(word)
    else:
        for match in _WORD_PATTERN.finditer(text):
            word = match.group().lower()
            if word not in _STOPWORDS and len(word) > 1:
                tokens.append(_stem_english(word))

    return tokens


def _token_to_id(token: str) -> int:
    """Convert token to positive 32-bit integer ID for Qdrant."""
    return hash(token) & 0x7FFFFFFF


def _get_idf(token_id: int, tf: int) -> float:
    """Get IDF score for a token.

    Uses global IDF from Redis if available; falls back to per-doc approximation.
    """
    global _global_idf, _global_idf_loaded

    # Try loading global IDF on first call (sync redis — this runs in sync context)
    if not _global_idf_loaded:
        _global_idf_loaded = True
        try:
            import redis as sync_redis

            from src.config_stub import settings

            r = sync_redis.from_url(settings.redis_url, decode_responses=True)
            raw = r.get("search:idf_stats")
            r.close()
            if raw:
                data = json.loads(raw)
                _global_idf = {int(k): float(v) for k, v in data.items()}
                logger.info("Loaded global IDF stats: %d tokens", len(_global_idf))
        except Exception:
            logger.debug("Global IDF not available — using per-doc approximation")
            _global_idf = None

    if _global_idf is not None and token_id in _global_idf:
        return _global_idf[token_id]

    # Fallback: per-document IDF approximation
    return math.log(1 + 1.0 / tf) + 1.0


def text_to_sparse_vector(
    text: str,
    service: str | None = None,
) -> dict[int, float]:
    """Convert text to a sparse vector using BM25-like TF-IDF scoring.

    Args:
        text: Document or query text.
        service: Service name for per-service avgdl (e.g. "memvault", "intelflow").
                 Defaults to generic avgdl=200 if not specified.

    Returns {token_hash: weight} suitable for Qdrant SparseVector.
    Token IDs are generated via hash to avoid maintaining a vocabulary.
    """
    tokens = tokenize(text)
    if not tokens:
        return {}

    default_avgdl = _SERVICE_AVGDL["default"]
    avgdl = _SERVICE_AVGDL.get(service, default_avgdl) if service else default_avgdl

    doc_len = len(tokens)
    tf_counts = Counter(tokens)
    sparse = {}

    for token, tf in tf_counts.items():
        # BM25 TF component: tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl/avgdl))
        tf_score = (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * doc_len / avgdl))

        # IDF: global if available, per-doc approximation otherwise
        token_id = _token_to_id(token)
        idf = _get_idf(token_id, tf)

        weight = tf_score * idf
        sparse[token_id] = weight

    return sparse


async def compute_corpus_idf(
    texts: list[str],
    total_docs: int | None = None,
) -> dict[int, float]:
    """Compute global IDF statistics from a corpus of texts.

    For use by a background job that periodically updates Redis.

    Args:
        texts: All document texts in the corpus.
        total_docs: Total document count (defaults to len(texts)).

    Returns:
        {token_id: idf_score} dict ready to store in Redis.
    """
    n = total_docs or len(texts)
    if n == 0:
        return {}

    # Count document frequency for each token
    df: Counter = Counter()
    for text in texts:
        tokens = set(tokenize(text))  # unique tokens per doc
        for token in tokens:
            token_id = _token_to_id(token)
            df[token_id] += 1

    # Compute IDF: log((N - df + 0.5) / (df + 0.5) + 1)
    idf_stats = {}
    for token_id, doc_freq in df.items():
        idf = math.log((n - doc_freq + 0.5) / (doc_freq + 0.5) + 1)
        idf_stats[token_id] = round(idf, 4)

    return idf_stats


async def store_idf_to_redis(idf_stats: dict[int, float]) -> bool:
    """Store computed IDF stats to Redis for runtime use.

    Called by background job (e.g. cron or lifecycle script).
    """
    try:
        from .redis import get_redis

        r = get_redis()
        # Store as JSON with string keys
        data = {str(k): v for k, v in idf_stats.items()}
        await r.set("search:idf_stats", json.dumps(data), ex=86400)  # 24h TTL

        # Reset in-memory cache so next call picks up fresh data
        global _global_idf, _global_idf_loaded
        _global_idf = None
        _global_idf_loaded = False

        logger.info("Stored IDF stats to Redis: %d tokens", len(idf_stats))
        return True
    except Exception as e:
        logger.error("Failed to store IDF stats: %s", e)
        return False
