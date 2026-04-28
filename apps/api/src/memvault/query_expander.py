"""Query Expander — HyDE-style query enhancement for better memory retrieval.

Problem: Short queries like "that Python tool" or "我們之前說的" produce poor
embeddings. HyDE generates a hypothetical ideal memory that would match,
then embeds that instead.

Modes:
  1. HyDE: LLM generates hypothetical answer → embed that
  2. Keyword expansion: Extract key terms + synonyms for keyword search
  3. Passthrough: Query is already specific enough, use as-is
"""

import logging
import re
from dataclasses import dataclass, field

from pydantic_ai import Agent

from sdk_client.timeout import dynamic_timeout

from .llm_config import get_litellm_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stop words — from shared single source of truth
# ---------------------------------------------------------------------------

from src.shared.text_utils import CJK_PATTERN as _CJK_RANGES  # noqa: E402
from src.shared.text_utils import STOPWORDS_EN as _EN_STOPWORDS  # noqa: E402
from src.shared.text_utils import STOPWORDS_ZH as _CJK_STOPWORDS  # noqa: E402

# ---------------------------------------------------------------------------
# Vague/demonstrative patterns that indicate expansion is useful
# ---------------------------------------------------------------------------

_VAGUE_EN = re.compile(
    r"\b(that|it|this|those|these|the thing|that thing|something|someone"
    r"|the one|what was|what is|which one|how do|how did"
    r"|previously|earlier|before|last time|ago)\b",
    re.IGNORECASE,
)

_VAGUE_CJK = re.compile(
    r"(之前|上次|那個|那件事|那時|哪個|哪種|什麼|那個工具|那個方法"
    r"|我們說的|我們之前|之前說|之前提|之前討論|記得|之前那個)"
)

_QUESTION_EN = re.compile(
    r"\b(what was|what is|what are|which|how do|how did|where is|where was)\b",
    re.IGNORECASE,
)

# Specific/technical token patterns (code identifiers, proper nouns)
_SPECIFIC_TOKENS = re.compile(r"[A-Z][a-z]+[A-Z]|[a-z]+_[a-z]+|`[^`]+`|\d{4,}|http[s]?://")

# ---------------------------------------------------------------------------
# Temporal resolution — resolve "上週", "last Monday" → date range
# ---------------------------------------------------------------------------


def _resolve_temporal_range(
    query: str, _now: "datetime | None" = None
) -> tuple[str | None, str | None]:
    """Extract a date range from temporal expressions in the query.

    Returns (date_from, date_to) as ISO date strings, or (None, None).

    Uses :func:`text_ops.normalize_temporal_range` which handles:
      - period expressions: 上週 / 上個月 / 去年 / 去年三月 / 上半年 / 上一季 / …
      - count-based ranges: 最近3天 / 最近一週 / 最近2個月 / …
      - cross-period chains: 去年一月到今年三月 / 上個月到本月 / …
      - single-date anchors: 今天 / 昨天 / 3天前 / 上週一 / …

    User preferences preserved:
      - **Single-date anchors** are expanded to **Sun-Sat** weeks (user pref:
        week starts on Sunday). This keeps the long-standing memvault behavior
        where "上週一" or "昨天" retrieves the whole week's blocks.
      - **Week-typed 7-day ranges** from queries explicitly mentioning
        週/周/禮拜/礼拜/week/星期 are shifted from Mon-Sun (upstream default)
        to Sun-Sat.
      - Month / year / quarter / half-year ranges remain as upstream emits them
        (full calendar period).
    """
    try:
        from datetime import datetime, timedelta

        from text_ops.temporal import normalize_temporal_range

        now = _now or datetime.now()
        expanded = normalize_temporal_range(query, now)
        if expanded == query:
            return None, None  # no temporal expression matched

        dates = re.findall(r"\d{4}-\d{2}-\d{2}", expanded)
        if not dates:
            return None, None

        dates.sort()
        date_from, date_to = dates[0], dates[-1]

        # Single-date anchor (e.g. "上週一", "昨天", "3天前") — preserve legacy
        # behavior by expanding to a Sun-Sat week centered on the anchor.
        if date_from == date_to:
            anchor = datetime.strptime(date_from, "%Y-%m-%d")
            days_since_sunday = anchor.isoweekday() % 7  # Sun=0, Mon=1, ..., Sat=6
            sunday = anchor - timedelta(days=days_since_sunday)
            saturday = sunday + timedelta(days=6)
            return sunday.strftime("%Y-%m-%d"), saturday.strftime("%Y-%m-%d")

        # Week-type range: exactly 7 days AND query mentions a week keyword →
        # shift upstream's Mon-Sun range to Sun-Sat (user preference).
        d_from = datetime.strptime(date_from, "%Y-%m-%d")
        d_to = datetime.strptime(date_to, "%Y-%m-%d")
        is_week_query = bool(re.search(r"[週周禮礼]|\bweek\b|星期", query, re.IGNORECASE))
        if is_week_query and (d_to - d_from).days == 6:
            days_since_sunday = d_from.isoweekday() % 7
            sunday = d_from - timedelta(days=days_since_sunday)
            saturday = sunday + timedelta(days=6)
            return sunday.strftime("%Y-%m-%d"), saturday.strftime("%Y-%m-%d")

        return date_from, date_to

    except Exception as e:
        logger.debug("temporal resolution failed: %s", e)
        return None, None


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class ExpandedQuery:
    original: str
    expanded_text: str  # For embedding (HyDE output or original)
    keywords: list[str] = field(default_factory=list)  # Extracted keywords for keyword search
    expansion_used: str = "passthrough"  # "hyde" | "keyword" | "passthrough"
    inferred_tags: list[str] = field(default_factory=list)  # NEW: domain routing tags
    # Temporal: resolved date range from relative expressions ("上週", "last Monday", …)
    temporal_from: str | None = None  # ISO date string e.g. "2026-04-03"
    temporal_to: str | None = None


# ---------------------------------------------------------------------------
# Domain tag inference for query routing
# ---------------------------------------------------------------------------

# Domain signal mapping for query routing
# Each domain has 5-7 signal keywords; query must match >= 2 to activate pre-filter
_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "finance": [
        "報表",
        "預算",
        "帳單",
        "訂閱",
        "營收",
        "支出",
        "expense",
        "budget",
        "subscription",
        "revenue",
        "invoice",
        "transaction",
    ],
    "devops": [
        "docker",
        "nginx",
        "deploy",
        "k8s",
        "pipeline",
        "伺服器",
        "server",
        "container",
        "ci/cd",
        "kubernetes",
    ],
    "ai": [
        "llm",
        "embedding",
        "model",
        "prompt",
        "rag",
        "向量",
        "token",
        "fine-tune",
        "inference",
        "語言模型",
    ],
    "frontend": [
        "react",
        "css",
        "component",
        "rsbuild",
        "pnpm",
        "layout",
        "typescript",
        "前端",
        "ui",
        "tailwind",
    ],
    "invest": [
        "股票",
        "etf",
        "portfolio",
        "殖利率",
        "dividend",
        "持股",
        "投資",
        "基金",
        "報酬率",
    ],
    "planning": [
        "排程",
        "schedule",
        "cronicle",
        "每日",
        "weekly",
        "daily",
        "todo",
        "task",
        "待辦",
    ],
}

_MIN_SIGNAL_MATCH = 2  # Minimum signals to activate domain pre-filter


def _infer_domain_tags(query: str) -> list[str]:
    """Infer domain tags from query for pre-filtering.

    Returns matching domain tags if >= _MIN_SIGNAL_MATCH signals found.
    Returns empty list if uncertain (safe fallback to full search).
    """
    query_lower = query.lower()
    matched = []
    for domain, signals in _DOMAIN_SIGNALS.items():
        count = sum(1 for s in signals if s.lower() in query_lower)
        if count >= _MIN_SIGNAL_MATCH:
            matched.append(domain)
    return matched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_expand(query: str) -> bool:
    """Determine if query needs expansion.

    Expand when:
    - Query is short (<30 chars for CJK, <50 for Latin)
    - Query contains pronouns/demonstratives ("that", "it", "那個", "之前的")
    - Query is a question form ("what was", "哪個")

    Don't expand when:
    - Query is already specific (contains proper nouns, code identifiers)
    - Query is a direct memory keyword match ("記住", "remember")
    - Query length > 100 chars (already detailed enough)
    """
    stripped = query.strip()

    # Too short to meaningfully expand
    if len(stripped) < 3:
        return False

    # Very long queries already contain enough context
    if len(stripped) > 100:
        return False

    # Queries with specific/technical tokens are already precise
    if _SPECIFIC_TOKENS.search(stripped):
        return False

    # Direct memory retrieval keywords — user knows what they want
    direct_memory_kw = ["記住", "memorize", "remember that", "store this", "save this"]
    lower = stripped.lower()
    if any(kw in lower for kw in direct_memory_kw):
        return False

    # Detect CJK dominance
    cjk_count = len(_CJK_RANGES.findall(stripped))
    is_cjk_dominant = cjk_count / max(len(stripped), 1) > 0.3

    # Short query thresholds
    if is_cjk_dominant and len(stripped) < 30:
        return True
    if not is_cjk_dominant and len(stripped) < 50:
        return True

    # Vague demonstratives / pronouns
    if _VAGUE_EN.search(stripped) or _VAGUE_CJK.search(stripped):
        return True

    # Question forms
    if _QUESTION_EN.search(stripped):
        return True

    return False


_hyde_agent = Agent(
    output_type=str,
    system_prompt=(
        "You are a memory retrieval assistant. Given a search query, generate a SHORT "
        "hypothetical memory entry (2-3 sentences) that would perfectly answer this query. "
        "Write it as if it were a stored memory note.\n\n"
        "Respond with ONLY the hypothetical memory text, nothing else. "
        "Match the language of the query."
    ),
    retries=2,
)


async def expand_query(query: str) -> ExpandedQuery:
    """Expand a query for better retrieval.

    1. Resolve temporal expressions ("上週", "last Monday") → date range
    2. Check if expansion is needed (should_expand)
    3. If yes, try HyDE via local LLM (oMLX port 8000)
    4. Extract keywords regardless
    5. Infer domain tags for pre-filtering
    6. Return ExpandedQuery

    Falls back to keyword-only expansion if LLM unavailable.
    """
    keywords = extract_keywords(query)
    inferred_tags = _infer_domain_tags(query)

    # Step 0: Resolve temporal expressions → date_from / date_to
    temporal_from, temporal_to = _resolve_temporal_range(query)

    if not should_expand(query):
        return ExpandedQuery(
            original=query,
            expanded_text=query,
            keywords=keywords,
            expansion_used="passthrough",
            inferred_tags=inferred_tags,
            temporal_from=temporal_from,
            temporal_to=temporal_to,
        )

    # Try HyDE via local LLM
    hypothetical = await _call_hyde_llm(query)

    if hypothetical and hypothetical.strip():
        hyde_text = hypothetical.strip()
        # Also extract keywords from the hypothetical document for richer keyword search
        hyde_keywords = extract_keywords(hyde_text)
        # Merge keywords: original first, then unique additions from hyde
        merged_keywords = list(keywords)
        seen = set(keywords)
        for kw in hyde_keywords:
            if kw not in seen:
                merged_keywords.append(kw)
                seen.add(kw)

        return ExpandedQuery(
            original=query,
            expanded_text=hyde_text,
            keywords=merged_keywords[:8],
            expansion_used="hyde",
            inferred_tags=inferred_tags,
            temporal_from=temporal_from,
            temporal_to=temporal_to,
        )

    # LLM unavailable — fall back to keyword-only expansion
    # Use original query but with enriched keyword set
    return ExpandedQuery(
        original=query,
        expanded_text=query,
        keywords=keywords,
        expansion_used="keyword",
        inferred_tags=inferred_tags,
        temporal_from=temporal_from,
        temporal_to=temporal_to,
    )


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text for keyword search.

    - Remove stop words (English + Chinese common ones)
    - Keep proper nouns, technical terms, numbers
    - For CJK text, keep 2+ char segments
    - Return top 5-8 keywords
    """
    stripped = text.strip()
    if not stripped:
        return []

    keywords: list[str] = []
    seen: set[str] = set()

    cjk_count = len(_CJK_RANGES.findall(stripped))
    has_cjk = cjk_count > 0

    if has_cjk:
        # Use jieba for CJK tokenization
        try:
            import logging as _logging

            import jieba

            jieba.setLogLevel(_logging.WARNING)
            tokens = list(jieba.cut(stripped))
        except ImportError:
            # Fallback: split on whitespace and extract CJK segments
            tokens = _fallback_cjk_tokenize(stripped)

        for token in tokens:
            token = token.strip()
            if not token:
                continue
            # Keep CJK tokens of 2+ chars that are not stopwords
            token_lower = token.lower()
            if _CJK_RANGES.search(token):
                if len(token) >= 2 and token not in _CJK_STOPWORDS and token not in seen:
                    keywords.append(token)
                    seen.add(token)
            else:
                # Mixed text: also keep Latin tokens
                if (
                    len(token) > 2
                    and token_lower not in _EN_STOPWORDS
                    and re.match(r"[a-zA-Z0-9]", token)
                    and token_lower not in seen
                ):
                    keywords.append(token)
                    seen.add(token_lower)
    else:
        # Pure Latin / English
        for match in re.finditer(r"[a-zA-Z0-9][a-zA-Z0-9_\-\.]*", stripped):
            token = match.group()
            token_lower = token.lower()
            if len(token) > 2 and token_lower not in _EN_STOPWORDS and token_lower not in seen:
                keywords.append(token)
                seen.add(token_lower)

    return keywords[:8]


def _fallback_cjk_tokenize(text: str) -> list[str]:
    """Simple fallback tokenizer when jieba is unavailable.

    Splits on whitespace and punctuation, preserving CJK character groups.
    """
    tokens: list[str] = []
    # Split on whitespace and common punctuation
    parts = re.split(
        r"[\s\u3000\uff0c\u3002\uff01\uff1f\u300c\u300d\u3010\u3011\uff0c\u3002\uff01\uff1f\u300c\u300d\u3010\u3011]+",
        text,
    )
    for part in parts:
        if part:
            # Extract CJK runs and Latin runs separately
            for segment in re.finditer(
                r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+|[a-zA-Z0-9_\-]+",
                part,
            ):
                tokens.append(segment.group())
    return tokens


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RLM-enhanced query expansion
# ---------------------------------------------------------------------------

_RLM_MIN_RESULTS_THRESHOLD = 3  # Trigger RLM iteration if fewer results than this


async def expand_query_rlm(
    query: str,
    initial_result_count: int = 0,
) -> ExpandedQuery:
    """RLM-enhanced query expansion — generates multiple hypothetical perspectives.

    Flow:
      1. Run standard expand_query() (single-shot HyDE)
      2. If initial_result_count >= 3, return as-is
      3. If < 3 results, escalate to RLM for recursive multi-perspective expansion
      4. On RLM failure, return standard HyDE result

    Args:
        query: The user's search query.
        initial_result_count: Number of results from initial retrieval (0 = unknown).

    Returns:
        ExpandedQuery with potentially richer expanded_text and keywords.
    """
    import asyncio

    # Step 1: Run standard HyDE
    hyde_result = await expand_query(query)

    # Step 2: Gate — only escalate if few results
    if initial_result_count >= _RLM_MIN_RESULTS_THRESHOLD:
        logger.debug(
            "expand_query_rlm: %d results >= %d, keeping hyde result",
            initial_result_count,
            _RLM_MIN_RESULTS_THRESHOLD,
        )
        return hyde_result

    # Step 3: Escalate to RLM
    logger.info(
        "expand_query_rlm: %d results < %d, escalating to RLM for query=%r",
        initial_result_count,
        _RLM_MIN_RESULTS_THRESHOLD,
        query,
    )

    try:
        rlm_result = await asyncio.to_thread(_run_rlm_expansion, query)
        if rlm_result:
            # Merge keywords from both sources
            merged_kw = list(hyde_result.keywords)
            seen = set(hyde_result.keywords)
            for kw in extract_keywords(rlm_result):
                if kw not in seen:
                    merged_kw.append(kw)
                    seen.add(kw)

            return ExpandedQuery(
                original=query,
                expanded_text=rlm_result,
                keywords=merged_kw[:10],
                expansion_used="rlm",
                inferred_tags=hyde_result.inferred_tags,
                temporal_from=hyde_result.temporal_from,
                temporal_to=hyde_result.temporal_to,
            )
    except Exception as exc:
        logger.warning("RLM query expansion failed — using hyde result: %s", exc)

    return hyde_result


def _run_rlm_expansion(query: str) -> str | None:
    """Run RLM query expansion synchronously (called via asyncio.to_thread).

    RLM generates multiple hypothetical memory entries from different perspectives,
    then synthesizes them into a single rich query representation.
    """
    from src.shared.rlm_engine import RLMConfig, RLMEngine

    config = RLMConfig(
        model="grok-4-fast",
        api_base="http://localhost:4000/v1",
        api_key="sk-litellm-local-dev",
        max_iterations=5,
        max_timeout_secs=60,
    )
    engine = RLMEngine(config)

    prompt = (
        "You are a memory retrieval assistant. The user's query returned too few results. "
        "Generate a SINGLE enriched hypothetical memory entry that would match this query.\n\n"
        "Strategy:\n"
        "1. Consider what the user might be looking for from multiple angles\n"
        "2. Think about different phrasings, related concepts, and synonyms\n"
        "3. Consider both English and Chinese terminology if relevant\n"
        "4. Synthesize into one concise hypothetical memory (3-5 sentences)\n\n"
        "Return ONLY the hypothetical memory text. Match the language of the query."
    )

    result = engine.completion(prompt=prompt, context=f"Query: {query}")

    if result.status != "ok":
        logger.warning("RLM expansion returned status=%s", result.status)
        return None

    text = result.response.strip()
    if not text or len(text) < 10:
        return None

    return text


async def _call_hyde_llm(query: str) -> str | None:
    """Call oMLX local LLM for HyDE query expansion. Returns None on failure.

    Uses PydanticAI agent with oMLX (port 8000), dynamic timeout scaled by
    query length, and built-in retries (2 attempts).
    """
    timeout = dynamic_timeout(base=5, factor=0.5, context=len(query) / 1000, cap=30)
    try:
        result = await _hyde_agent.run(
            query,
            model=await get_litellm_model(),
            model_settings={"temperature": 0.3, "max_tokens": 200, "timeout": timeout},
        )
        return result.output
    except Exception:
        logger.debug("oMLX LLM unavailable — falling back to keyword mode")
    return None
