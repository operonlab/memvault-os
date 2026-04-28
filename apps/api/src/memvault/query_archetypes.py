"""QueryClassifyOp archetypes — real-usage-driven intent classification data.

Three deliverables consumed by QueryClassifyOp:
  1. INTENT_ARCHETYPES  — representative queries per intent (Tier 2 semantic vectors)
  2. KEYWORD_SUPPLEMENTS — additional keyword patterns (Tier 1 enrichment)
  3. PRESET_QA           — high-frequency query templates with optimized retrieval hints

Design rationale: Derived from actual high-frequency queries observed in daily usage.
The four most common query families are:
  - 最近忙什麼 (temporal activity)
  - 什麼時間做了哪些事 (timeline)
  - X 做到哪裡了 (progress status)
  - 根據之前討論的 X，下一步怎麼規劃 (continuation planning)

These all map to existing intents (EXPLORATORY, ENTITY_LOOKUP, CONCEPTUAL)
but the keyword patterns were only ~30% coverage on these real queries.
"""

from __future__ import annotations

import re

# ═══════════════════════════════════════════════════════════════════════════
# 1. INTENT ARCHETYPES — Tier 2 semantic vector comparison
# ═══════════════════════════════════════════════════════════════════════════
#
# Each intent has 6-10 representative queries. At startup, these are
# pre-embedded and cached. Incoming queries are compared via cosine
# similarity to find the closest intent cluster.
#
# Selection criteria:
#   - Real queries observed in daily usage (not theoretical)
#   - Cover both 中文 and English phrasing
#   - Include the tricky edge cases that keyword-only would misclassify

INTENT_ARCHETYPES: dict[str, list[str]] = {
    "entity_lookup": [
        "memvault 是什麼",
        "什麼是 QueryClassifyOp",
        "recall.py 在哪裡",
        "nodeflow 的 DAG executor 是哪個檔案",
        "Qdrant 目前用哪個 collection",
        "scoring pipeline 裡的 Trust Penalty 是做什麼的",
        "what is the cascade recall service",
        "Dream Loop 的五個階段是哪些",
    ],
    "factual": [
        "memvault 的 port 是多少",
        "目前有幾個 core module",
        "Qdrant 的 similarity metric 用什麼",
        "scoring pipeline 有幾個 stage",
        "workshop 的 main branch 叫什麼",
        "L1 community 用什麼演算法分群",
        "哪些 module 用到 Qdrant",
        "embedding 維度是多少",
    ],
    "conceptual": [
        "為什麼選擇 modular monolith 而不是 microservice",
        "memvault 的 scoring pipeline 設計原則是什麼",
        "CLT 在管線中怎麼體現",
        "為什麼要用這個 port 範圍的架構",
        "根據之前討論的架構，下一步怎麼規劃",
        "我們之前決定用什麼策略處理時態衝突",
        "上次聊到 recall.py 和 query_runtime 的整合方向是什麼",
        "之前討論的 KG lint 四層遞進還缺什麼",
        "接著上次的結論，下一步怎麼做",
        "based on our previous discussion, what should we do next",
    ],
    "exploratory": [
        "最近忙什麼",
        "這週做了哪些事",
        "上週的進度如何",
        "最近有什麼新的 commit",
        "我最近在研究什麼",
        "這陣子 workshop 有什麼進展",
        "什麼時間做了哪些事情",
        "memvault 做到哪裡了",
        "目前什麼事情做到一半",
        "nodeflow 現在進度怎樣",
        "有哪些東西還沒做完",
        "recently been working on what",
    ],
    "cross_domain": [
        "memvault 和 docvault 怎麼整合",
        "auth 和 notification 之間的事件流是什麼",
        "capture 和 memvault 的資料怎麼流動",
        "KG triple 和 scoring pipeline 之間的關係",
        "intelflow 的 RSS 怎麼接到 memvault",
        "compare memvault and docvault approaches",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# 2. KEYWORD SUPPLEMENTS — Tier 1 pattern enrichment
# ═══════════════════════════════════════════════════════════════════════════
#
# These patterns supplement the existing _RECENCY/_FACTUAL/_CONCEPTUAL/etc.
# in query_router.py. They cover the gap between theoretical intent
# categories and real-world query phrasing.

# Activity / temporal — "最近忙什麼", "什麼時間做了哪些事"
# Maps to: EXPLORATORY
ACTIVITY_PATTERNS = re.compile(
    r"(忙什麼|在做什麼|做了什麼事|做了哪些|有什麼進展|什麼時間做了"
    r"|activity|what.*doing|been.*working|recent changes|最近的改動"
    r"|這段時間|這陣子|有在做|動了什麼|改了什麼)",
    re.IGNORECASE,
)

# Progress / status — "X 做到哪裡了", "什麼事情做到一半"
# Maps to: EXPLORATORY (with entity context if entity found)
PROGRESS_PATTERNS = re.compile(
    r"(做到哪|做到哪裡|做到什麼程度|進展到哪|完成了嗎|完成度|差多少|還剩"
    r"|未完成|做到一半|in progress|how far along|remaining|left to do"
    r"|pending|todo|status update|目前狀態|現在狀態|進度如何|進度怎樣)",
    re.IGNORECASE,
)

# Continuation / planning — "根據之前討論的X，下一步怎麼規劃"
# Maps to: CONCEPTUAL (requires retrieving prior discussion context)
CONTINUATION_PATTERNS = re.compile(
    r"(之前討論|上次聊|接著做|下一步|接下來|根據之前|我們之前|延續"
    r"|based on.*discussion|next step|continue from|following up|pick up where"
    r"|之前說的|之前決定|回到.*話題|上次的結論|接著上次|承接之前"
    r"|之前提到|剛剛提到的|回去看|按照我們說的)",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# 3. PRESET QA — high-frequency query templates with retrieval hints
# ═══════════════════════════════════════════════════════════════════════════
#
# For the highest-frequency queries, provide optimized retrieval hints
# so the pipeline can take a fast path. Each entry specifies:
#   - pattern: compiled regex to match the query
#   - intent: forced intent classification (bypasses scoring)
#   - retrieval_hint: guidance for the retrieval layer
#   - sort_by: preferred result ordering
#   - time_window_days: temporal scope (0 = all time)
#   - layer_priority: which KG layers to prioritize

PRESET_QA: list[dict] = [
    {
        "name": "recent_activity",
        "pattern": re.compile(
            r"^(最近|這週|這陣子|上週)?(忙什麼|做了什麼|在做什麼|有什麼進展|動了什麼)",
            re.IGNORECASE,
        ),
        "intent": "exploratory",
        "retrieval_hint": "temporal_activity",
        "sort_by": "created_at_desc",
        "time_window_days": 7,
        "layer_priority": ["blocks", "triples"],
    },
    {
        "name": "timeline",
        "pattern": re.compile(
            r"(什麼時間|哪天|哪時候|when).*做了(什麼|哪些)",
            re.IGNORECASE,
        ),
        "intent": "exploratory",
        "retrieval_hint": "timeline",
        "sort_by": "created_at_asc",
        "time_window_days": 30,
        "layer_priority": ["blocks", "triples"],
    },
    {
        "name": "progress_status",
        "pattern": re.compile(
            r"(做到哪|做到哪裡|進度|進展|完成了嗎|狀態)(了|如何|怎樣|怎麼樣)?$"
            r"|.{2,20}(做到哪|進度如何|進度怎樣|完成度)",
            re.IGNORECASE,
        ),
        "intent": "exploratory",
        "retrieval_hint": "progress",
        "sort_by": "updated_at_desc",
        "time_window_days": 0,
        "layer_priority": ["triples", "summaries", "blocks"],
    },
    {
        "name": "continuation_planning",
        "pattern": re.compile(
            r"(之前討論|上次聊|根據之前|我們之前|之前決定|上次的結論)"
            r".*?(下一步|接下來|怎麼做|怎麼規劃|接著|然後呢)",
            re.IGNORECASE,
        ),
        "intent": "conceptual",
        "retrieval_hint": "continuation",
        "sort_by": "relevance",
        "time_window_days": 0,
        "layer_priority": ["summaries", "triples", "blocks"],
    },
    {
        "name": "what_is_pending",
        "pattern": re.compile(
            r"(什麼|哪些)(事情|東西|工作)?(還沒做|做到一半|未完成|pending|in progress)",
            re.IGNORECASE,
        ),
        "intent": "exploratory",
        "retrieval_hint": "pending_items",
        "sort_by": "updated_at_desc",
        "time_window_days": 14,
        "layer_priority": ["blocks", "triples"],
    },
]


def match_preset_qa(query: str) -> dict | None:
    """Match query against preset QA patterns. Returns first match or None."""
    for preset in PRESET_QA:
        if preset["pattern"].search(query):
            return preset
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 4. TIER 2 — Archetype Embedding Infrastructure
# ═══════════════════════════════════════════════════════════════════════════
#
# Lazy-initialized singleton: pre-embed all INTENT_ARCHETYPES queries,
# store per-intent embedding lists. At query time, compare against each
# archetype and take max similarity per intent (not centroid average).
#
# Max-similarity > centroid because a single close archetype match is
# more meaningful than average distance to all archetypes. Centroid
# dilutes signal when archetypes cover diverse phrasings.

import asyncio
import logging

logger = logging.getLogger(__name__)

_archetype_embeddings: dict[str, list[list[float]]] | None = None
_init_lock = asyncio.Lock()


async def ensure_archetype_embeddings() -> dict[str, list[list[float]]]:
    """Pre-embed all archetype queries and store per-intent embedding lists.

    Lazy singleton — first call embeds ~40 queries (~5s), subsequent calls
    return cached embeddings instantly. Thread-safe via asyncio.Lock.

    Returns dict of {intent: [embedding_vectors]} or empty dict on failure.
    """
    global _archetype_embeddings
    if _archetype_embeddings is not None:
        return _archetype_embeddings

    async with _init_lock:
        if _archetype_embeddings is not None:
            return _archetype_embeddings

        from .embedding import get_embeddings_batch

        cache: dict[str, list[list[float]]] = {}
        for intent, queries in INTENT_ARCHETYPES.items():
            embeddings = await get_embeddings_batch(queries, task_type="classification")
            valid = [e for e in embeddings if e is not None]
            if not valid:
                logger.warning("No valid embeddings for intent %s", intent)
                continue
            cache[intent] = valid
            logger.debug("Archetype embeddings for %s: %d vectors", intent, len(valid))

        _archetype_embeddings = cache
        logger.info("Archetype embeddings initialized: %d intents, %d total vectors",
                     len(cache), sum(len(v) for v in cache.values()))
        return cache


async def semantic_intent_scores(query: str) -> dict[str, float]:
    """Tier 2: Compare query embedding against all archetypes, take max per intent.

    Max-similarity avoids centroid dilution: "為什麼用這個 port 範圍架構" matches
    conceptual archetype "為什麼要用這個 port 範圍的架構" perfectly, but centroid
    averages this out with 9 other unrelated conceptual archetypes.

    Returns {intent: max_cosine_similarity} or empty dict on failure.
    """
    from src.shared.scoring_stages import cosine_similarity

    from .embedding import get_embedding

    query_emb = await get_embedding(query, task_type="search_query")
    if query_emb is None:
        return {}

    all_embeddings = await ensure_archetype_embeddings()
    if not all_embeddings:
        return {}

    scores: dict[str, float] = {}
    for intent, embeddings in all_embeddings.items():
        max_sim = max(cosine_similarity(query_emb, arch_emb) for arch_emb in embeddings)
        scores[intent] = max_sim
    return scores


# ═══════════════════════════════════════════════════════════════════════════
# 5. TIER 3 — LLM Intent Classification (fallback)
# ═══════════════════════════════════════════════════════════════════════════

from pydantic_ai import Agent

from .llm_models import IntentClassificationOutput

_intent_agent = Agent(
    output_type=IntentClassificationOutput,
    system_prompt=(
        "You are classifying the intent of a knowledge retrieval query.\n\n"
        "Intent types:\n"
        "- entity_lookup: Looking up a specific entity (what is X, where is Y)\n"
        "- factual: Seeking precise facts (how many, what port, what version)\n"
        "- conceptual: Understanding principles, reasoning, planning "
        "(why, how should, based on previous discussion, next steps)\n"
        "- exploratory: Open-ended exploration (recent activity, progress, "
        "what's been happening)\n"
        "- cross_domain: Bridging multiple topics (how does X relate to Y)\n"
        "- unknown: Cannot determine intent\n\n"
        "Respond with the most appropriate intent and your confidence."
    ),
    retries=2,
)


async def llm_classify(query: str) -> tuple[str, float] | None:
    """Tier 3: LLM-based intent classification. Only called on low confidence.

    Returns (intent_str, confidence) or None on failure.
    """
    try:
        from .llm_config import get_litellm_model

        result = await _intent_agent.run(
            query,
            model=await get_litellm_model(),
            model_settings={"temperature": 0.0, "max_tokens": 64, "timeout": 5},
        )
        output = result.output
        return (output.intent, output.confidence)
    except Exception as exc:
        logger.warning("Tier 3 LLM classify failed (%s) — falling back", exc)
        return None
    return None
