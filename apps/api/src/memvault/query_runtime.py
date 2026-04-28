"""Composable query runtime for fast/slow memvault access."""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.events_stub.bus import Event, event_bus
from src.events_stub.types import MemvaultEvents
from src.shared.prefetch import PrefetchFingerprint

from .embedding import get_embedding
from .injection_guard import sanitize_for_injection
from .kg_services import cascade_recall_service
from .models import MemoryBlock
from .schemas import (
    MemoryCard,
    MemoryEvidenceRef,
    MemoryInjectResponse,
    MemoryInspectResponse,
    MemoryQueryRequest,
    MemoryQueryResponse,
    MemoryQueryStrategy,
)
from .scoring_pipeline import ScoringConfig, scoring_config_for_intent
from .services import memory_block_service

logger = logging.getLogger(__name__)

_TASK_MODES = {"auto", "lookup", "decide", "build", "reflect"}
_THINKING_MODES = {"auto", "fast", "slow"}
_LOAD_BUDGETS = {"light", "standard", "deep"}
_CONSUMERS = {"agent", "human", "ui"}

# Hard prompt budget — keeps system prompt cache-friendly and cost-predictable.
# Inspired by Hermes Agent memory injection design.
PROMPT_BUDGET_CHARS = 2000

# Reuse the same SpeculativePrefetchCache instance from slow_thinker
# to keep metrics unified (reviewer finding #8)
from .slow_thinker import _prefetch_cache


def _normalize(value: str, allowed: set[str], default: str) -> str:
    cleaned = (value or "").strip().lower()
    return cleaned if cleaned in allowed else default


def choose_thinking_mode(
    task_mode: str,
    thinking_mode: str,
    load_budget: str,
    consumer: str,
    intent: str = "unknown",
) -> str:
    """Choose a concrete thinking mode from intent — consumer is irrelevant.

    Design: QueryClassifyOp determines intent, intent determines thinking mode.
    Consumer/entry-point only affects output format, never retrieval depth.
    """
    thinking_mode = _normalize(thinking_mode, _THINKING_MODES, "auto")

    # Explicit override: user requested fast or slow directly
    if thinking_mode != "auto":
        return thinking_mode

    # Intent-based routing (replaces consumer-based routing)
    _INTENT_TO_THINKING: dict[str, str] = {
        "entity_lookup": "fast",  # 查實體不需要 cascade
        "factual": "fast",  # 查事實不需要 cascade
        "conceptual": "slow",  # 需要 KG 摘要+三元組
        "exploratory": "slow",  # 需要完整記憶脈絡
        "cross_domain": "slow",  # 需要跨領域 cascade
        "unknown": "slow",  # 不確定就全搜
    }
    return _INTENT_TO_THINKING.get(intent, "slow")


def _budget_config(load_budget: str) -> dict[str, int]:
    normalized = _normalize(load_budget, _LOAD_BUDGETS, "standard")
    if normalized == "light":
        return {"search_top_k": 8, "fast": 7, "cascade": 2}
    if normalized == "deep":
        return {"search_top_k": 16, "fast": 13, "cascade": 6}
    return {"search_top_k": 12, "fast": 10, "cascade": 4}


def _clip(text: str | None, limit: int = 180) -> str:
    value = (text or "").strip().replace("\n", " ")
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _freshness_label(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    now = datetime.now(UTC)
    delta = now - ts.astimezone(UTC)
    hours = delta.total_seconds() / 3600
    if hours < 12:
        return "今天"
    if hours < 48:
        return "近兩天"
    days = delta.days
    if days < 14:
        return "近兩週"
    if days < 60:
        return "近兩月"
    return "較早"


def _unique_cards(cards: Iterable[MemoryCard]) -> list[MemoryCard]:
    seen: set[str] = set()
    deduped: list[MemoryCard] = []
    for card in cards:
        if card.id in seen:
            continue
        seen.add(card.id)
        deduped.append(card)
    return deduped


def _block_title(block_type: str, tags: list[str], source_session: str | None) -> str:
    if tags:
        return f"{block_type} / {tags[0]}"
    if source_session:
        return f"{block_type} / {source_session[:12]}"
    return f"{block_type} memory"


def _task_use_now(task_mode: str, source_type: str, summary: str) -> str:
    if task_mode == "lookup":
        return f"直接提取可驗證資訊：{_clip(summary, 80)}"
    if task_mode == "decide":
        return "把這張卡當成決策依據，先比對是否符合目前取捨。"
    if task_mode == "reflect":
        return "把這張卡視為長期模式，再與其他證據交叉驗證。"
    if source_type == "attitude":
        return "延續這個偏好或工作原則，除非有新的明確反例。"
    return f"把這張卡當作當前工作的直接上下文：{_clip(summary, 70)}"


def _block_card(block, layer: str, task_mode: str, score: float | None = None) -> MemoryCard:
    safe_content = sanitize_for_injection(block.content)
    title = _block_title(block.block_type, block.tags or [], block.source_session)
    # Skill blocks in fast layer use a short index summary (80 chars);
    # full content is available on-demand via inspect mode — mirrors Hermes index separation.
    is_skill_index = block.block_type == "skill" and layer == "fast"
    clip_limit = 80 if is_skill_index else 180
    if is_skill_index:
        why = (
            f"技能索引（完整內容請用 inspect mode）：{', '.join(block.tags[:3])}"
            if block.tags
            else "技能索引（完整內容請用 inspect mode）"
        )
    elif block.tags:
        why = f"命中標籤 {', '.join(block.tags[:3])}，適合作為{layer}上下文。"
    else:
        why = "這是與查詢最接近的原始記憶區塊。"
    return MemoryCard(
        id=f"{layer}:block:{block.id}",
        title=title,
        summary=_clip(safe_content, clip_limit),
        why_relevant=why,
        use_now=_task_use_now(task_mode, "block", safe_content),
        layer=layer,
        source_type="block",
        confidence=round(float(score if score is not None else block.confidence or 0.55), 3),
        freshness=_freshness_label(block.created_at),
        tags=list(block.tags or []),
        evidence_refs=[
            MemoryEvidenceRef(
                kind="block",
                ref_id=str(block.id),
                title=title,
                snippet=_clip(safe_content, 120),
                score=round(float(score), 3) if score is not None else None,
            )
        ],
    )


def _block_result_to_attitude_dict(result) -> dict:
    """Convert a SemanticSearchResult with block_type='attitude' to attitude dict."""
    block = result.block
    return {
        "id": str(block.id),
        "fact": block.content or "",
        "category": (block.tags or ["preference"])[0],
        "confidence": float(block.confidence or 0.5),
        "score": float(result.score or 0.0),
        "freshness": block.updated_at.isoformat() if block.updated_at else None,
    }


def _attitude_card(attitude: dict, layer: str, task_mode: str) -> MemoryCard:
    fact = sanitize_for_injection(attitude["fact"])
    category = attitude.get("category", "preference")
    title = f"偏好 / {category}"
    return MemoryCard(
        id=f"{layer}:attitude:{attitude.get('id', fact[:32])}",
        title=title,
        summary=_clip(fact, 180),
        why_relevant="這是目前仍有效的工作偏好或做事原則。",
        use_now=_task_use_now(task_mode, "attitude", fact),
        layer=layer,
        source_type="attitude",
        confidence=round(float(attitude.get("score") or attitude.get("confidence") or 0.5), 3),
        freshness=attitude.get("freshness"),
        tags=[category],
        evidence_refs=[
            MemoryEvidenceRef(
                kind="attitude",
                ref_id=str(attitude.get("id", title)),
                title=title,
                snippet=_clip(fact, 120),
                score=round(float(attitude.get("score") or 0.0), 3),
            )
        ],
    )


def _summary_card(summary, layer: str, task_mode: str) -> MemoryCard:
    text = sanitize_for_injection(summary.summary)
    key_findings = summary.key_findings or []
    use_now = key_findings[0] if key_findings else _task_use_now(task_mode, "summary", text)
    return MemoryCard(
        id=f"{layer}:summary:{summary.id}",
        title="聚合摘要",
        summary=_clip(text, 180),
        why_relevant="這是多筆記憶聚合後的較低負荷知識摘要。",
        use_now=_clip(use_now, 120),
        layer=layer,
        source_type="summary",
        confidence=0.72,
        freshness=_freshness_label(summary.updated_at),
        tags=list(summary.tags or []),
        evidence_refs=[
            MemoryEvidenceRef(
                kind="summary",
                ref_id=str(summary.id),
                title="聚合摘要",
                snippet=_clip(text, 120),
            )
        ],
    )


def _triple_card(triple, layer: str, task_mode: str) -> MemoryCard:
    text = f"{triple.subject} --[{triple.predicate}]--> {triple.object}"
    return MemoryCard(
        id=f"{layer}:triple:{triple.id}",
        title="知識關聯",
        summary=_clip(text, 180),
        why_relevant="這是可追溯的結構化知識關聯，適合慢想驗證。",
        use_now=_task_use_now(task_mode, "triple", text),
        layer=layer,
        source_type="triple",
        confidence=0.68,
        freshness=_freshness_label(triple.updated_at),
        tags=[triple.predicate],
        evidence_refs=[
            MemoryEvidenceRef(
                kind="triple",
                ref_id=str(triple.id),
                title=triple.predicate,
                snippet=_clip(text, 120),
            )
        ],
    )


async def _search_blocks(
    db: AsyncSession,
    space_id: str,
    query: str,
    top_k: int,
    scoring_config: ScoringConfig | None = None,
    intent: str = "unknown",
) -> tuple[list, dict]:
    embedding = await get_embedding(query, task_type="search_query")
    if embedding:
        qdrant_result = await memory_block_service.qdrant_search(
            db,
            space_id,
            query,
            embedding,
            top_k=top_k,
            scoring_config=scoring_config,
            intent=intent,
        )
        if qdrant_result is not None:
            results, meta = qdrant_result
            return results, {"backend": meta.backend or "qdrant", "input_count": meta.input_count}

        results, meta = await memory_block_service.semantic_search(
            db,
            space_id,
            embedding,
            top_k=top_k,
            query=query,
            scoring_config=scoring_config,
            intent=intent,
        )
        return results, {
            "backend": meta.backend or "pgvector-fallback",
            "input_count": meta.input_count,
        }

    results = await memory_block_service.text_search(db, space_id, query, top_k=top_k)
    if results:
        return results, {"backend": "text", "input_count": len(results)}

    # Final fallback: recent memory blocks from DB when all search backends return empty
    q = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(top_k)
    )
    recent_rows = (await db.execute(q)).scalars().all()
    recent_results = [memory_block_service.to_response(row) for row in recent_rows]
    return recent_results, {"backend": "recent-fallback", "input_count": len(recent_results)}


async def _check_prefetch_cache(
    space_id: str,
    consumer: str,
    task_mode: str,
    query: str,
    top_k: int,
) -> tuple[list[MemoryCard] | None, float]:
    """Check speculative prefetch cache. Returns (cards, check_ms) or (None, check_ms).

    Cache hits are re-sanitized before returning (defense-in-depth).
    """
    _t_check = _time.monotonic()
    # Use a lightweight intent guess — avoid importing query_router which
    # pulls in query_expander with a broken import path (shared.text_utils).
    # The prefetch write path uses IntentPredictorOp's transition rules,
    # so we only need to match the same 3 stable fields.
    intent_guess = task_mode  # coarse heuristic: task_mode correlates with intent
    try:
        from .query_router import classify_query_full

        plan = await classify_query_full(query)
        intent_guess = plan.intent.value
    except Exception:
        pass  # graceful degradation — use task_mode as intent proxy
    fp = PrefetchFingerprint(
        module="memvault",
        space_id=space_id,
        fields={
            "consumer": consumer,
            "task_mode": task_mode,
            "intent": intent_guess,
        },
    )
    cached = await _prefetch_cache.get(fp)
    if not cached:
        await _prefetch_cache.record_miss(space_id)
        return None, (_time.monotonic() - _t_check) * 1000

    # Re-sanitize cached cards (defense-in-depth, reviewer HIGH #4)
    safe_cards = []
    for c in cached:
        c["summary"] = sanitize_for_injection(c.get("summary", ""))
        c.setdefault("source", "speculative_prefetch")
        safe_cards.append(
            MemoryCard(**{k: v for k, v in c.items() if k in MemoryCard.model_fields})
        )

    check_ms = (_time.monotonic() - _t_check) * 1000
    await _prefetch_cache.record_hit(space_id, latency_saved_ms=check_ms)
    return safe_cards, check_ms


def _merge_prefetch_cards(
    existing: list[MemoryCard],
    prefetched: list[MemoryCard],
    budget: int,
) -> list[MemoryCard]:
    """Merge prefetch cards AFTER stable results. Dedup by ID, respect budget."""
    seen = {c.id for c in existing}
    merged = list(existing)
    for c in prefetched:
        if c.id not in seen and len(merged) < budget:
            seen.add(c.id)
            merged.append(c)
    return merged


async def _run_query_with_pipeline(
    db: AsyncSession,
    space_id: str,
    request: MemoryQueryRequest,
) -> MemoryQueryResponse:
    """Pipeline path: QueryRouteOp → QueryExpandOp → search → (slow: RerankOp → CRAGEvalOp).

    Adds query expansion, intent-tuned scoring, and optional CRAG evaluation on top of the
    standard sequential search logic.  All failures are isolated — pre-pipeline failure
    falls back to defaults; post-pipeline failure is skipped silently.
    """
    _t_start = _time.monotonic()

    # Build pipeline objects (config from env)
    from .pipeline_config import MemvaultPipelineConfig
    from .pipelines.query_pipeline import (
        build_query_post_pipeline,
        build_query_pre_pipeline,
    )

    config = MemvaultPipelineConfig.from_env()
    pre_pipeline = build_query_pre_pipeline(config)
    post_pipeline = build_query_post_pipeline(config)

    # --- Pre-search phase ---
    intent_value: str = "unknown"
    search_query: str = request.q
    task_mode_from_intent: str = "build"

    try:
        pre_ctx = await pre_pipeline.execute({"query": request.q})

        # Extract intent (QueryIntent enum → str)
        raw_intent = pre_ctx.get("intent")
        if raw_intent is not None:
            intent_value = (
                str(raw_intent.value) if hasattr(raw_intent, "value") else str(raw_intent)
            )

        # Map intent → task_mode (mirrors sequential path)
        try:
            from .query_router import QueryIntent

            intent_to_task: dict = {
                QueryIntent.ENTITY_LOOKUP: "lookup",
                QueryIntent.FACTUAL: "lookup",
                QueryIntent.CONCEPTUAL: "reflect",
                QueryIntent.EXPLORATORY: "reflect",
                QueryIntent.CROSS_DOMAIN: "decide",
                QueryIntent.UNKNOWN: "build",
            }
            raw_intent_obj = pre_ctx.get("intent")
            task_mode_from_intent = intent_to_task.get(raw_intent_obj, "build")
        except Exception:
            logger.debug("query pipeline: intent extraction failed, using default")

        # Use expanded query text when available
        expanded = pre_ctx.get("expanded_query")
        if expanded is not None and hasattr(expanded, "expanded_text") and expanded.expanded_text:
            search_query = expanded.expanded_text

    except Exception:
        # Pre-pipeline failed: fall back to original query + unknown intent
        logger.warning("query pre-pipeline failed, falling back to defaults", exc_info=True)
        intent_value = "unknown"
        search_query = request.q
        task_mode_from_intent = "build"

    task_mode = _normalize(task_mode_from_intent, _TASK_MODES, "build")
    intent_scoring = scoring_config_for_intent(intent_value)

    thinking_mode_requested = _normalize(request.thinking_mode, _THINKING_MODES, "auto")
    load_budget = _normalize(request.load_budget, _LOAD_BUDGETS, "standard")
    consumer = _normalize(request.consumer, _CONSUMERS, "human")
    thinking_mode_used = choose_thinking_mode(
        task_mode=task_mode,
        thinking_mode=thinking_mode_requested,
        load_budget=load_budget,
        consumer=consumer,
        intent=intent_value,
    )
    budget = _budget_config(load_budget)

    # Phase B2: Check speculative prefetch cache
    prefetch_hit, _prefetch_check_ms = await _check_prefetch_cache(
        space_id,
        consumer,
        task_mode,
        request.q,
        request.top_k,
    )

    # --- Search phase (inline, same as sequential path) ---
    if prefetch_hit:
        fast_cards = prefetch_hit[: budget["fast"]]
        search_results = []
        search_meta = {"backend": "prefetch-cache", "input_count": len(prefetch_hit)}
    else:
        search_results, search_meta = await _search_blocks(
            db,
            space_id,
            search_query,
            top_k=max(request.top_k, budget["search_top_k"]),
            scoring_config=intent_scoring,
            intent=intent_value,
        )
        # attitude blocks flow through the same qdrant_search — KAS: Block = SSoT
        fast_cards = [
            (
                _attitude_card(_block_result_to_attitude_dict(result), "fast", task_mode)
                if result.block.block_type == "attitude"
                else _block_card(result.block, "fast", task_mode, result.score)
            )
            for result in search_results[: budget["fast"]]
        ]
        fast_cards = _unique_cards(fast_cards)[: request.top_k]

    cascade_cards: list[MemoryCard] = []
    layers_searched: list[str] = []
    evaluation_verdict: str | None = None
    cascade_result = None

    if thinking_mode_used == "slow":
        cascade_result = await cascade_recall_service.recall(
            db,
            space_id,
            request.q,
            top_k=budget["cascade"],
            evaluate=getattr(request, "evaluate", "default"),
            mode=getattr(request, "retrieval_mode", "auto"),
        )
        cascade_cards.extend(
            _summary_card(item, "cascade", task_mode) for item in cascade_result.summaries
        )
        cascade_cards.extend(
            _triple_card(item, "cascade", task_mode)
            for item in cascade_result.triples[: budget["cascade"]]
        )
        cascade_cards.extend(
            _block_card(item, "cascade", task_mode)
            for item in cascade_result.blocks[: max(1, budget["cascade"] // 2)]
        )
        cascade_cards = _unique_cards(cascade_cards)[: budget["cascade"]]
        layers_searched = cascade_result.layers_searched
        evaluation_verdict = cascade_result.evaluation_verdict

    # --- Post-search phase (slow thinking only) ---
    verdict: str | None = None
    confidence_score: float | None = None
    evaluation_meta: dict | None = None

    if thinking_mode_used == "slow" and cascade_result is not None:
        try:
            post_ctx = await post_pipeline.execute(
                {
                    "query": request.q,
                    "results": cascade_result,
                    "intent": intent_value,
                }
            )
            verdict = post_ctx.get("verdict")
            confidence_score = post_ctx.get("confidence_score")
            evaluation_meta = post_ctx.get("evaluation_meta")
        except Exception:
            # Post-pipeline failure: skip CRAG evaluation, continue without it
            logger.debug("query post-pipeline failed, skipping CRAG", exc_info=True)

    strategy = MemoryQueryStrategy(
        task_mode=task_mode,
        thinking_mode_requested=thinking_mode_requested,
        thinking_mode_used=thinking_mode_used,
        load_budget=load_budget,
        consumer=consumer,
    )
    highlights = [card.use_now for card in fast_cards[:2]]
    if not highlights:
        highlights = [card.summary for card in fast_cards]

    metadata: dict = {
        "backend": search_meta["backend"],
        "layers_searched": layers_searched,
        "evaluation_verdict": evaluation_verdict,
        "pipeline": True,
    }
    if verdict is not None:
        metadata["crag_verdict"] = verdict
    if confidence_score is not None:
        metadata["crag_confidence"] = confidence_score
    if evaluation_meta is not None:
        metadata["crag_meta"] = evaluation_meta

    response = MemoryQueryResponse(
        query=request.q,
        strategy=strategy,
        cards=fast_cards,
        cascade_cards=cascade_cards,
        highlights=highlights,
        metadata=metadata,
    )

    # Slow Thinker: fire-and-forget query completion event
    _t_end = _time.monotonic()
    event_bus.publish_fire_and_forget(
        Event(
            type=MemvaultEvents.QUERY_COMPLETED,
            data={
                "space_id": space_id,
                "query": request.q,
                "consumer": consumer,
                "task_mode": task_mode,
                "thinking_mode_used": thinking_mode_used,
                "load_budget": load_budget,
                "intent": intent_value,
                "tags": [c.tags[0] for c in fast_cards[:3] if c.tags],
                "result_count": len(fast_cards),
                "latency_ms": round((_t_end - _t_start) * 1000, 1),
            },
            source="memvault",
        )
    )

    return response


async def run_memory_query(
    db: AsyncSession,
    space_id: str,
    request: MemoryQueryRequest,
    use_pipeline: bool = False,
) -> MemoryQueryResponse:
    if use_pipeline:
        return await _run_query_with_pipeline(db, space_id, request)

    _t_start = _time.monotonic()

    # Scale-graded strategy — detect knowledge volume and adapt
    try:
        from .scale_service import KnowledgeScale, detect_scale

        scale = await detect_scale(db, space_id)
    except Exception:
        scale = KnowledgeScale.MEDIUM  # safe default

    task_mode = _normalize(request.task_mode, _TASK_MODES, "auto")

    # task_mode=auto → infer from query content via classify_query()
    # AttnRes-inspired: also derive intent-tuned ScoringConfig
    intent_value: str = "unknown"
    if task_mode == "auto":
        try:
            from .query_router import QueryIntent, classify_query_full

            plan = await classify_query_full(request.q)
            intent_value = plan.intent.value
            intent_to_task: dict[str, str] = {
                QueryIntent.ENTITY_LOOKUP: "lookup",
                QueryIntent.FACTUAL: "lookup",
                QueryIntent.CONCEPTUAL: "reflect",
                QueryIntent.EXPLORATORY: "reflect",
                QueryIntent.CROSS_DOMAIN: "decide",
                QueryIntent.UNKNOWN: "build",
            }
            task_mode = intent_to_task.get(plan.intent, "build")
        except Exception:
            task_mode = "build"

    intent_scoring = scoring_config_for_intent(intent_value)

    thinking_mode_requested = _normalize(request.thinking_mode, _THINKING_MODES, "auto")
    load_budget = _normalize(request.load_budget, _LOAD_BUDGETS, "standard")
    consumer = _normalize(request.consumer, _CONSUMERS, "human")
    thinking_mode_used = choose_thinking_mode(
        task_mode=task_mode,
        thinking_mode=thinking_mode_requested,
        load_budget=load_budget,
        consumer=consumer,
        intent=intent_value,
    )
    budget = _budget_config(load_budget)

    # Phase B2: Check speculative prefetch cache before expensive search
    prefetch_hit, _prefetch_check_ms = await _check_prefetch_cache(
        space_id,
        consumer,
        task_mode,
        request.q,
        request.top_k,
    )

    if prefetch_hit:
        fast_cards = prefetch_hit[: budget["fast"]]
        search_results = []
        search_meta = {"backend": "prefetch-cache", "input_count": len(prefetch_hit)}
    else:
        search_results, search_meta = await _search_blocks(
            db,
            space_id,
            request.q,
            top_k=max(request.top_k, budget["search_top_k"]),
            scoring_config=intent_scoring,
            intent=intent_value,
        )
        # attitude blocks flow through the same qdrant_search — KAS: Block = SSoT
        fast_cards = [
            (
                _attitude_card(_block_result_to_attitude_dict(result), "fast", task_mode)
                if result.block.block_type == "attitude"
                else _block_card(result.block, "fast", task_mode, result.score)
            )
            for result in search_results[: budget["fast"]]
        ]
        fast_cards = _unique_cards(fast_cards)[: request.top_k]

    cascade_cards: list[MemoryCard] = []
    layers_searched: list[str] = []
    evaluation_verdict: str | None = None
    if thinking_mode_used == "slow":
        cascade = await cascade_recall_service.recall(
            db,
            space_id,
            request.q,
            top_k=budget["cascade"],
            evaluate=getattr(request, "evaluate", "default"),
            mode=getattr(request, "retrieval_mode", "auto"),
        )
        cascade_cards.extend(
            _summary_card(item, "cascade", task_mode) for item in cascade.summaries
        )
        cascade_cards.extend(
            _triple_card(item, "cascade", task_mode)
            for item in cascade.triples[: budget["cascade"]]
        )
        cascade_cards.extend(
            _block_card(item, "cascade", task_mode)
            for item in cascade.blocks[: max(1, budget["cascade"] // 2)]
        )
        cascade_cards = _unique_cards(cascade_cards)[: budget["cascade"]]
        layers_searched = cascade.layers_searched
        evaluation_verdict = cascade.evaluation_verdict

    strategy = MemoryQueryStrategy(
        task_mode=task_mode,
        thinking_mode_requested=thinking_mode_requested,
        thinking_mode_used=thinking_mode_used,
        load_budget=load_budget,
        consumer=consumer,
    )
    highlights = [card.use_now for card in fast_cards[:2]]
    if not highlights:
        highlights = [card.summary for card in fast_cards]

    response = MemoryQueryResponse(
        query=request.q,
        strategy=strategy,
        cards=fast_cards,
        cascade_cards=cascade_cards,
        highlights=highlights,
        metadata={
            "backend": search_meta["backend"],
            "layers_searched": layers_searched,
            "evaluation_verdict": evaluation_verdict,
            "knowledge_scale": scale.value,
        },
    )

    # Slow Thinker: fire-and-forget query completion event
    _t_end = _time.monotonic()
    event_bus.publish_fire_and_forget(
        Event(
            type=MemvaultEvents.QUERY_COMPLETED,
            data={
                "space_id": space_id,
                "query": request.q,
                "consumer": consumer,
                "task_mode": task_mode,
                "thinking_mode_used": thinking_mode_used,
                "load_budget": load_budget,
                "intent": intent_value,
                "tags": [c.tags[0] for c in fast_cards[:3] if c.tags],
                "result_count": len(fast_cards),
                "latency_ms": round((_t_end - _t_start) * 1000, 1),
            },
            source="memvault",
        )
    )

    return response


def build_injection_payload(response: MemoryQueryResponse) -> MemoryInjectResponse:
    agent_cards = response.cards[:3]
    original_card_count = len(agent_cards)

    def _build_lines(cards: list, include_use_now: bool) -> list[str]:
        lines = ["[Memvault Fast Memory]"]
        for idx, card in enumerate(cards, start=1):
            lines.append(f"{idx}. {card.title}: {card.summary}")
            if include_use_now:
                lines.append(f"   Use now: {card.use_now}")
        return lines

    # Phase 1: full output
    prompt_lines = _build_lines(agent_cards, include_use_now=True)
    system_prompt_memory = "\n".join(prompt_lines)

    if len(system_prompt_memory) > PROMPT_BUDGET_CHARS:
        # Phase 2: drop use_now lines
        prompt_lines = _build_lines(agent_cards, include_use_now=False)
        system_prompt_memory = "\n".join(prompt_lines)

    if len(system_prompt_memory) > PROMPT_BUDGET_CHARS:
        # Phase 3: clip each summary to 80 chars
        clipped_lines = ["[Memvault Fast Memory]"]
        for idx, card in enumerate(agent_cards, start=1):
            clipped_lines.append(f"{idx}. {card.title}: {_clip(card.summary, 80)}")
        system_prompt_memory = "\n".join(clipped_lines)

    if len(system_prompt_memory) > PROMPT_BUDGET_CHARS:
        # Phase 4: drop cards from the end until within budget
        while len(agent_cards) > 1 and len(system_prompt_memory) > PROMPT_BUDGET_CHARS:
            agent_cards = agent_cards[:-1]
            clipped_lines = ["[Memvault Fast Memory]"]
            for idx, card in enumerate(agent_cards, start=1):
                clipped_lines.append(f"{idx}. {card.title}: {_clip(card.summary, 80)}")
            system_prompt_memory = "\n".join(clipped_lines)

    decision_bias = [card.summary for card in response.cards if card.source_type == "attitude"][:3]
    working_context = sorted(
        response.cards,
        key=lambda c: c.freshness or "",
        reverse=True,
    )[:3]
    working_context = [card.summary for card in working_context]

    budget_meta: dict = {
        "prompt_budget_chars": PROMPT_BUDGET_CHARS,
        "prompt_used_chars": len(system_prompt_memory),
        "cards_trimmed": original_card_count - len(agent_cards),
    }
    merged_metadata = {**(response.metadata or {}), **budget_meta}

    return MemoryInjectResponse(
        query=response.query,
        strategy=response.strategy,
        system_prompt_memory=system_prompt_memory,
        working_context=working_context,
        decision_bias=decision_bias,
        cards=agent_cards,
        metadata=merged_metadata,
    )


def build_inspect_payload(response: MemoryQueryResponse) -> MemoryInspectResponse:
    raw_sections = {
        "fast": [ref for card in response.cards for ref in card.evidence_refs],
        "cascade": [ref for card in response.cascade_cards for ref in card.evidence_refs],
    }
    return MemoryInspectResponse(
        query=response.query,
        strategy=response.strategy,
        cards=[*response.cards, *response.cascade_cards],
        raw_sections=raw_sections,
        metadata=response.metadata,
    )
