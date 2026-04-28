"""Adaptive Query Router — regex-based intent classification for cascade recall layer selection.

Classifies queries into intent types and produces a LayerPlan that tells
CascadeRecallService which layers to search and in what mode (SEMANTIC/HYBRID/ILIKE/SKIP).

Design constraints:
  - <1ms latency (pure regex + keyword scoring, no LLM)
  - Reuses _DOMAIN_SIGNALS, _SPECIFIC_TOKENS, _VAGUE_EN/CJK from query_expander.py
  - Confidence < 0.4 → fallback to full scan (unknown intent)
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from .query_archetypes import (
    ACTIVITY_PATTERNS,
    CONTINUATION_PATTERNS,
    PROGRESS_PATTERNS,
    llm_classify,
    match_preset_qa,
    semantic_intent_scores,
)
from .query_expander import (
    _CJK_RANGES,
    _SPECIFIC_TOKENS,
    _VAGUE_CJK,
    _VAGUE_EN,
    extract_keywords,
)


class QueryIntent(StrEnum):
    """Query intent categories for layer routing."""

    ENTITY_LOOKUP = "entity_lookup"
    CONCEPTUAL = "conceptual"
    FACTUAL = "factual"
    EXPLORATORY = "exploratory"
    CROSS_DOMAIN = "cross_domain"
    UNKNOWN = "unknown"


@dataclass
class LayerPlan:
    """Which layers to search and in what mode."""

    intent: QueryIntent
    confidence: float
    # Layer → search mode: SEMANTIC | HYBRID | ILIKE | SKIP
    layers: dict[str, str] = field(default_factory=dict)
    # Preset QA hints (set when matched by match_preset_qa)
    preset_hint: str | None = None
    time_window_days: int = 0
    sort_by: str = "relevance"


# ---------------------------------------------------------------------------
# Intent patterns
# ---------------------------------------------------------------------------

# Recency / temporal patterns → exploratory
_RECENCY_PATTERNS = re.compile(
    r"(最近|上週|上個月|今天|昨天|這週|recently|last week|last month|today|yesterday"
    r"|this week|past \d+ days|latest|newest|我最近|近期|剛剛|剛才)",
    re.IGNORECASE,
)

# Cross-domain patterns (bridging topics)
_CROSS_DOMAIN_PATTERNS = re.compile(
    r"(跨|之間|整合|結合|cross|between|integrate|combine|versus|vs\.?"
    r"|比較|對比|compare|intersection|overlap|關聯|連結|bridge)",
    re.IGNORECASE,
)

# Factual / precise patterns
_FACTUAL_PATTERNS = re.compile(
    r"(port|端口|密碼|password|config|設定|版本|version|IP|URL|地址|路徑|path"
    r"|多少|幾個|哪裡|where|what is the|exactly|precisely|specifically"
    r"|\d+\.\d+|\d{4,}|localhost)",
    re.IGNORECASE,
)

# Conceptual / high-level patterns
_CONCEPTUAL_PATTERNS = re.compile(
    r"(原則|principle|architecture|架構|philosophy|哲學|strategy|策略|pattern|模式"
    r"|best practice|最佳實踐|approach|方法論|design|設計|why|為什麼|概念|concept"
    r"|理念|思維|paradigm|methodology|framework|框架|guideline|準則)",
    re.IGNORECASE,
)

# Exploratory / open-ended patterns
_EXPLORATORY_PATTERNS = re.compile(
    r"(學了什麼|learned|做了什麼|summary|summarize|overview|總結|回顧|review"
    r"|progress|進度|status|狀態|how.*going|怎麼樣|explore|探索|discovery)",
    re.IGNORECASE,
)

# Entity-like patterns (specific names, identifiers)
_ENTITY_PATTERNS = re.compile(
    r"([A-Z][a-z]+(?:[A-Z][a-z]+)+|[a-z]+[-_][a-z]+|"  # CamelCase, kebab/snake
    r"[A-Z]{2,}|"  # Acronyms
    r"[a-z]+\.\w+)",  # dotted identifiers
)

# CJK entity extraction: 2-4 char proper noun-like terms (used for PPR seeds)
_CJK_ENTITY_RE = re.compile(r"[\u4e00-\u9fff]{2,4}")


def extract_query_entities(query: str) -> list[str]:
    """Extract entity-like mentions from a query string.

    Combines ASCII patterns (CamelCase, kebab-case, acronyms)
    with CJK multi-char terms. Used as PPR seed entities.
    """
    entities: list[str] = []
    entities.extend(_ENTITY_PATTERNS.findall(query))
    entities.extend(_CJK_ENTITY_RE.findall(query))
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for e in entities:
        lower = e.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(e)
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_intent(query: str, keywords: list[str]) -> dict[QueryIntent, float]:
    """Score each intent based on pattern matches and keyword analysis."""
    scores: dict[QueryIntent, float] = {intent: 0.0 for intent in QueryIntent}
    query_lower = query.lower()

    # Pattern-based scoring
    if _RECENCY_PATTERNS.search(query):
        scores[QueryIntent.EXPLORATORY] += 0.5

    if _CROSS_DOMAIN_PATTERNS.search(query):
        scores[QueryIntent.CROSS_DOMAIN] += 0.5

    if _FACTUAL_PATTERNS.search(query):
        scores[QueryIntent.FACTUAL] += 0.5

    if _CONCEPTUAL_PATTERNS.search(query):
        scores[QueryIntent.CONCEPTUAL] += 0.5

    if _EXPLORATORY_PATTERNS.search(query):
        scores[QueryIntent.EXPLORATORY] += 0.4

    # Supplementary patterns from real usage (query_archetypes.py)
    if ACTIVITY_PATTERNS.search(query):
        scores[QueryIntent.EXPLORATORY] += 0.45
    if PROGRESS_PATTERNS.search(query):
        scores[QueryIntent.EXPLORATORY] += 0.4
    if CONTINUATION_PATTERNS.search(query):
        scores[QueryIntent.CONCEPTUAL] += 0.45

    # Entity detection
    entity_matches = _ENTITY_PATTERNS.findall(query)
    specific_matches = _SPECIFIC_TOKENS.findall(query)
    if entity_matches or specific_matches:
        scores[QueryIntent.ENTITY_LOOKUP] += 0.3 * len(entity_matches)
        scores[QueryIntent.ENTITY_LOOKUP] += 0.2 * len(specific_matches)

    # Short, specific queries with no vague language → entity lookup
    cjk_count = len(_CJK_RANGES.findall(query))
    is_short = (cjk_count > 0 and len(query) < 15) or (cjk_count == 0 and len(query.split()) <= 3)

    if is_short and not _VAGUE_EN.search(query) and not _VAGUE_CJK.search(query):
        scores[QueryIntent.ENTITY_LOOKUP] += 0.3

    # Keyword count → more keywords suggests factual/entity
    if len(keywords) >= 3:
        scores[QueryIntent.FACTUAL] += 0.15

    # Vague language → exploratory
    if _VAGUE_EN.search(query) or _VAGUE_CJK.search(query):
        scores[QueryIntent.EXPLORATORY] += 0.3

    # Question words boost
    if re.search(r"\b(what|how|why|which|when)\b", query_lower):
        if re.search(r"\b(why|how should|how to)\b", query_lower):
            scores[QueryIntent.CONCEPTUAL] += 0.2
        elif re.search(r"\b(what is|which|where|when)\b", query_lower):
            scores[QueryIntent.FACTUAL] += 0.2

    return scores


# ---------------------------------------------------------------------------
# Layer mapping
# ---------------------------------------------------------------------------

# QueryIntent → layer search modes
_LAYER_MATRIX: dict[QueryIntent, dict[str, str]] = {
    QueryIntent.ENTITY_LOOKUP: {
        "summaries": "SKIP",
        "communities": "ILIKE",
        "triples": "HYBRID",
        "blocks": "SKIP",
    },
    QueryIntent.CONCEPTUAL: {
        "summaries": "SEMANTIC",
        "communities": "SEMANTIC",
        "triples": "SKIP",
        "blocks": "SKIP",
    },
    QueryIntent.FACTUAL: {
        "summaries": "SKIP",
        "communities": "SKIP",
        "triples": "HYBRID",
        "blocks": "HYBRID",
    },
    QueryIntent.EXPLORATORY: {
        "summaries": "SKIP",
        "communities": "SKIP",
        "triples": "SEMANTIC",
        "blocks": "HYBRID",
    },
    QueryIntent.CROSS_DOMAIN: {
        "summaries": "SKIP",
        "communities": "ILIKE",
        "triples": "HYBRID",
        "blocks": "SKIP",
    },
    QueryIntent.UNKNOWN: {
        "summaries": "SEMANTIC",
        "communities": "SEMANTIC",
        "triples": "HYBRID",
        "blocks": "HYBRID",
    },
}

# Intent → RetrievalMode mapping (LightRAG-inspired)
_INTENT_TO_MODE: dict[QueryIntent, str] = {
    QueryIntent.ENTITY_LOOKUP: "local",
    QueryIntent.FACTUAL: "local",
    QueryIntent.CONCEPTUAL: "global",
    QueryIntent.EXPLORATORY: "global",
    QueryIntent.CROSS_DOMAIN: "hybrid",
    QueryIntent.UNKNOWN: "hybrid",
}


def intent_to_retrieval_mode(intent: QueryIntent) -> str:
    """Map a QueryIntent to a RetrievalMode string."""
    return _INTENT_TO_MODE.get(intent, "hybrid")


def _route_confidence_threshold(query_len: int) -> float:
    """Dynamic routing confidence threshold based on query length.

    Short queries (< 20 chars) are inherently ambiguous — lower the bar so
    the router doesn't immediately fall back to full-scan.
    query_len is capped at 50 to avoid unbounded growth. Clamped to [0.25, 0.6].
    """
    return max(0.25, min(0.6, 0.35 + 0.005 * min(query_len, 50)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_query(query: str) -> LayerPlan:
    """Classify a query and return the layer plan.

    Fast path: preset QA patterns bypass scoring for high-frequency queries.
    Normal path: keyword + pattern scoring with dynamic confidence threshold.
    """
    # Fast path: preset QA match (high-frequency queries get optimized routing)
    preset = match_preset_qa(query)
    if preset:
        forced_intent = QueryIntent(preset["intent"])
        plan = LayerPlan(
            intent=forced_intent,
            confidence=0.95,
            layers=_LAYER_MATRIX[forced_intent].copy(),
        )
        plan.preset_hint = preset.get("retrieval_hint")
        plan.time_window_days = preset.get("time_window_days", 0)
        plan.sort_by = preset.get("sort_by", "relevance")
        return plan

    keywords = extract_keywords(query)
    scores = _score_intent(query, keywords)

    # Find the best intent
    best_intent = max(scores, key=lambda k: scores[k])
    best_score = scores[best_intent]

    confidence_threshold = _route_confidence_threshold(len(query))

    # Low confidence → unknown (full scan)
    if best_score < confidence_threshold:
        return LayerPlan(
            intent=QueryIntent.UNKNOWN,
            confidence=best_score,
            layers=_LAYER_MATRIX[QueryIntent.UNKNOWN].copy(),
        )

    # Normalize confidence to 0-1 range (cap at 1.0)
    confidence = min(best_score, 1.0)

    return LayerPlan(
        intent=best_intent,
        confidence=round(confidence, 3),
        layers=_LAYER_MATRIX[best_intent].copy(),
    )


async def classify_query_full(query: str) -> LayerPlan:
    """Tier 1∥2 parallel fusion + Tier 3 LLM fallback.

    - Preset QA fast path (unchanged, bypasses all tiers)
    - Tier 1: keyword scoring (sync, <1ms)
    - Tier 2: semantic vector vs archetype centroids (async, ~5ms)
    - Fusion: weighted average (0.4 keyword + 0.6 semantic)
    - Tier 3: LLM classification (async, ~500ms, only on low fused confidence)

    Falls back gracefully: embedding down → Tier 1 only; LLM down → Tier 1+2 only.
    """
    # Fast path: preset QA match
    preset = match_preset_qa(query)
    if preset:
        forced_intent = QueryIntent(preset["intent"])
        plan = LayerPlan(
            intent=forced_intent,
            confidence=0.95,
            layers=_LAYER_MATRIX[forced_intent].copy(),
        )
        plan.preset_hint = preset.get("retrieval_hint")
        plan.time_window_days = preset.get("time_window_days", 0)
        plan.sort_by = preset.get("sort_by", "relevance")
        return plan

    # Tier 1: keyword scoring (sync, instant)
    keywords = extract_keywords(query)
    tier1_scores = _score_intent(query, keywords)

    # Tier 2: semantic scoring (async, ~5ms)
    tier2_scores = await semantic_intent_scores(query)

    # Fusion: weighted average when Tier 2 is available
    if tier2_scores:
        all_intents = set(tier1_scores) | set(tier2_scores)
        fused = {
            intent: 0.4 * tier1_scores.get(intent, 0.0) + 0.6 * tier2_scores.get(intent, 0.0)
            for intent in all_intents
        }
    else:
        fused = tier1_scores

    best_intent = max(fused, key=lambda k: fused[k])
    best_score = fused[best_intent]
    threshold = _route_confidence_threshold(len(query))

    # Tier 3: LLM fallback (fused confidence still low)
    if best_score < threshold:
        llm_result = await llm_classify(query)
        if llm_result:
            intent_str, conf = llm_result
            return LayerPlan(
                intent=QueryIntent(intent_str),
                confidence=conf,
                layers=_LAYER_MATRIX[QueryIntent(intent_str)].copy(),
            )
        return LayerPlan(
            intent=QueryIntent.UNKNOWN,
            confidence=best_score,
            layers=_LAYER_MATRIX[QueryIntent.UNKNOWN].copy(),
        )

    return LayerPlan(
        intent=best_intent,
        confidence=round(min(best_score, 1.0), 3),
        layers=_LAYER_MATRIX[best_intent].copy(),
    )


# ---------------------------------------------------------------------------
# Personalized Query Router
# ---------------------------------------------------------------------------


class PersonalizedQueryRouter:
    """Wraps classify_query() with user-specific attention data.

    Adjusts layer routing based on the user's historical query patterns:
    1. Query mentions an 'active' entity → boost triples (user is going deep)
    2. Query mentions a 'fading' entity → boost summaries (user is reviewing)
    3. Unknown intent + dominant historical intent → bias toward that intent
    4. Low confidence → double personalization weight (compensate for regex uncertainty)

    Falls back to base classify_query() when no attention data is available.
    """

    def __init__(self, attention_profile: dict | None = None):
        self.attention = attention_profile or {}

    def classify(self, query: str) -> LayerPlan:
        """Classify query with personalization overlay (Tier 1 only)."""
        base = classify_query(query)
        if not self.attention:
            return base
        return self._adjust_layers(query, base)

    async def classify_full(self, query: str) -> LayerPlan:
        """Classify query with Tier 1∥2 + Tier 3 + personalization overlay."""
        base = await classify_query_full(query)
        if not self.attention:
            return base
        return self._adjust_layers(query, base)

    def _adjust_layers(self, query: str, base: LayerPlan) -> LayerPlan:
        """Apply personalization rules to the base layer plan."""
        query_lower = query.lower()
        adjusted = LayerPlan(
            intent=base.intent,
            confidence=base.confidence,
            layers=dict(base.layers),
        )

        # Find which attention entities are mentioned in the query
        active_mentioned = []
        fading_mentioned = []
        for entity, level in self.attention.items():
            if entity.lower() in query_lower:
                if level == "active":
                    active_mentioned.append(entity)
                elif level == "fading":
                    fading_mentioned.append(entity)

        # Rule 1: Active entity mentioned → boost triples (deep exploration)
        if active_mentioned:
            if adjusted.layers.get("triples") == "SKIP":
                adjusted.layers["triples"] = "HYBRID"
            elif adjusted.layers.get("triples") == "SEMANTIC":
                adjusted.layers["triples"] = "HYBRID"

        # Rule 2: Fading entity mentioned → boost summaries (review mode)
        if fading_mentioned:
            if adjusted.layers.get("summaries") == "SKIP":
                adjusted.layers["summaries"] = "SEMANTIC"
            if adjusted.layers.get("communities") == "SKIP":
                adjusted.layers["communities"] = "SEMANTIC"

        # Rule 3: Unknown intent + dominant historical intent → bias
        if base.intent == QueryIntent.UNKNOWN and base.confidence < _route_confidence_threshold(
            len(query)
        ):
            dominant = self._get_dominant_intent()
            if dominant and dominant in _LAYER_MATRIX:
                # Merge dominant intent layers (don't override non-SKIP)
                for layer, mode in _LAYER_MATRIX[dominant].items():
                    if adjusted.layers.get(layer) == "SKIP" and mode != "SKIP":
                        adjusted.layers[layer] = mode

        # Rule 4: Low confidence → apply all non-SKIP more aggressively
        if base.confidence < 0.5 and (active_mentioned or fading_mentioned):
            for layer in adjusted.layers:
                if adjusted.layers[layer] == "SKIP":
                    adjusted.layers[layer] = "SEMANTIC"

        return adjusted

    def _get_dominant_intent(self) -> QueryIntent | None:
        """Infer dominant intent from attention profile composition.

        If 70%+ of entities are in one attention level, suggest the corresponding intent.
        """
        if not self.attention:
            return None

        level_counts: dict[str, int] = {}
        for level in self.attention.values():
            level_counts[level] = level_counts.get(level, 0) + 1

        total = sum(level_counts.values())
        if total == 0:
            return None

        # If mostly active entities → user is doing deep research → factual/entity
        active_ratio = level_counts.get("active", 0) / total
        if active_ratio >= 0.7:
            return QueryIntent.FACTUAL

        # If mostly fading → user is reviewing → exploratory
        fading_ratio = level_counts.get("fading", 0) / total
        if fading_ratio >= 0.7:
            return QueryIntent.EXPLORATORY

        return None
