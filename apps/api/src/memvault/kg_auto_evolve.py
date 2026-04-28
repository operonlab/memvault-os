"""KG Auto-Evolution — Graphiti-style real-time triple extraction (P5).

When new MemoryBlocks are stored, automatically extract and upsert triples.
Subscribes to MemvaultEvents.MEMORY_STORED and runs triple extraction via
local LLM (oMLX), then feeds results into the existing TripleService pipeline.
"""

import logging

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from src.events_stub.bus import Event, event_bus
from src.events_stub.types import MemvaultEvents

from .kg_config import PREDICATE_VOCABULARY, VALID_PREDICATES, normalize_predicate
from .kg_schemas import TripleBatchCreate, TripleCreate
from .llm_config import make_deepseek_model
from .llm_models import TripleExtractionOutput

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_CONTENT_LENGTH = 50
_SKIP_BLOCK_TYPES = {"general"}  # too noisy for auto-extraction

# Predicate hint text per block_type
_PREDICATE_HINTS: dict[str, str] = {
    "attitude": (
        "Focus on predicates that reveal user preferences and beliefs: "
        "should, should_NOT, chosen_over, reason_for, pattern_is."
    ),
    "skill": (
        "Focus on predicates that reveal capability and tooling: "
        "uses, requires, implemented_as, configured_with, enables."
    ),
    "knowledge": ("Use all available predicates as appropriate for the content."),
}
_DEFAULT_PREDICATE_HINT = "Use all available predicates as appropriate for the content."


def _build_predicate_list() -> str:
    """Build a human-readable predicate list for the LLM prompt."""
    lines = []
    for category, predicates in PREDICATE_VOCABULARY.items():
        lines.append(f"  [{category}]: {', '.join(predicates)}")
    return "\n".join(lines)


_PREDICATE_LIST_TEXT = _build_predicate_list()

_triple_agent = Agent(
    output_type=TripleExtractionOutput,
    system_prompt=(
        "You are a knowledge graph triple extractor. "
        "Extract factual subject-predicate-object triples from the given text. "
        "ONLY use predicates from the approved vocabulary below — do not invent new ones.\n\n"
        f"APPROVED PREDICATES:\n{_PREDICATE_LIST_TEXT}\n\n"
        "Extract 1-5 high-quality triples. Do not hallucinate — only extract facts clearly "
        "stated or strongly implied by the text."
    ),
    retries=2,
)


# ---------------------------------------------------------------------------
# Triple extraction
# ---------------------------------------------------------------------------


async def extract_triples_from_content(
    content: str,
    block_type: str,
) -> list[dict[str, str]]:
    """Extract subject-predicate-object triples from memory content via LLM.

    Uses PydanticAI with DeepSeek (configurable via env) and constrained predicate
    vocabulary. Returns a list of dicts with subject/predicate/object/topic keys.
    Falls back to empty list on failure.
    """
    predicate_hint = _PREDICATE_HINTS.get(block_type, _DEFAULT_PREDICATE_HINT)
    user_prompt = (
        f"EXTRACTION GUIDANCE: {predicate_hint}\n\n"
        f"TEXT:\n{content}\n\n"
        "Extract triples."
    )

    try:
        result = await _triple_agent.run(
            user_prompt,
            model=make_deepseek_model(),
            model_settings={"temperature": 0.1, "max_tokens": 512, "timeout": 20},
        )

        # Validate and filter against approved predicate vocabulary
        valid_triples: list[dict[str, str]] = []
        for t in result.output.triples:
            subj = t.subject.strip()
            pred = t.predicate.strip()
            obj = t.object.strip()
            topic = (t.topic or "").strip() or None

            if not (subj and pred and obj):
                continue

            canonical = normalize_predicate(pred)
            if canonical not in VALID_PREDICATES:
                logger.debug("Skipping unknown predicate %r (normalized: %r)", pred, canonical)
                continue

            valid_triples.append(
                {"subject": subj, "predicate": canonical, "object": obj, "topic": topic}
            )

        logger.debug(
            "extract_triples_from_content: %d valid / %d raw (block_type=%s)",
            len(valid_triples),
            len(result.output.triples),
            block_type,
        )
        return valid_triples

    except Exception:
        logger.warning("Triple extraction failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# RLM-enhanced triple extraction
# ---------------------------------------------------------------------------

_RLM_CONTENT_THRESHOLD = 500  # Only use RLM for content longer than this


def _run_rlm_triple_extraction(content: str, block_type: str) -> list[dict[str, str]]:
    """Run RLM triple extraction synchronously (called via asyncio.to_thread).

    For complex/long content, RLM recursively:
    1. Segments content into thematic chunks
    2. Extracts triples from each chunk
    3. Performs entity resolution across chunks (dedup similar subjects/objects)
    4. Returns unified triple set
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

    predicate_hint = _PREDICATE_HINTS.get(block_type, _DEFAULT_PREDICATE_HINT)

    prompt = (
        "You are a knowledge graph triple extractor analyzing a complex memory block.\n\n"
        "Your task:\n"
        "1. Identify all distinct entities (subjects and objects) in the text\n"
        "2. Resolve entity aliases (e.g., 'RLM' and 'Recursive Language Model' are the same)\n"
        "3. Extract factual subject-predicate-object triples\n"
        "4. Assign a short topic (≤5 words) to each triple\n\n"
        f"APPROVED PREDICATES:\n{_PREDICATE_LIST_TEXT}\n\n"
        f"EXTRACTION GUIDANCE: {predicate_hint}\n\n"
        "ONLY use predicates from the approved list above.\n"
        "Extract 3-10 high-quality triples. Do not hallucinate.\n\n"
        "Return ONLY a JSON array. Each element: "
        '{"subject": "...", "predicate": "...", "object": "...", "topic": "..."}'
    )

    result = engine.completion(prompt=prompt, context=f"TEXT:\n{content}")

    if result.status != "ok":
        raise RuntimeError(f"RLM returned status={result.status}")

    from src.shared.llm_json import parse_llm_json

    triples_raw = parse_llm_json(result.response)
    if not isinstance(triples_raw, list):
        raise RuntimeError(f"Expected list from RLM, got {type(triples_raw)}")

    # Validate and filter using same logic as extract_triples_from_content
    valid: list[dict[str, str]] = []
    for t in triples_raw:
        if not isinstance(t, dict):
            continue
        subj = str(t.get("subject", "")).strip()
        pred = str(t.get("predicate", "")).strip()
        obj = str(t.get("object", "")).strip()
        topic = str(t.get("topic", "")).strip() or None

        if not (subj and pred and obj):
            continue

        canonical = normalize_predicate(pred)
        if canonical not in VALID_PREDICATES:
            logger.debug("RLM: skipping unknown predicate %r (normalized: %r)", pred, canonical)
            continue

        valid.append(
            {
                "subject": subj,
                "predicate": canonical,
                "object": obj,
                "topic": topic,
            }
        )

    return valid


async def extract_triples_rlm(
    content: str,
    block_type: str,
) -> list[dict[str, str]]:
    """RLM-enhanced triple extraction — for complex content > 500 chars.

    Flow:
      1. If content <= 500 chars, delegate to extract_triples_from_content()
      2. If content > 500 chars, use RLM for recursive entity resolution
      3. On RLM failure, fallback to extract_triples_from_content()

    Same return type as extract_triples_from_content().
    """
    import asyncio

    # Gate: simple content uses existing method
    if len(content) <= _RLM_CONTENT_THRESHOLD:
        return await extract_triples_from_content(content, block_type)

    logger.info(
        "extract_triples_rlm: content len=%d > %d, using RLM (block_type=%s)",
        len(content),
        _RLM_CONTENT_THRESHOLD,
        block_type,
    )

    try:
        triples = await asyncio.to_thread(_run_rlm_triple_extraction, content, block_type)
        logger.info(
            "extract_triples_rlm: %d triples extracted via RLM (block_type=%s)",
            len(triples),
            block_type,
        )
        return triples

    except Exception as exc:
        logger.warning("RLM triple extraction failed — falling back to oMLX: %s", exc)
        return await extract_triples_from_content(content, block_type)


# ---------------------------------------------------------------------------
# KG evolution orchestration
# ---------------------------------------------------------------------------


async def auto_evolve_kg(
    memory_id: str,
    content: str,
    block_type: str,
    space_id: str,
    source_session: str | None,
    db: AsyncSession,
) -> dict[str, int]:
    """Extract triples from a new MemoryBlock and feed them into TripleService.

    Calls extract_triples_from_content, then feeds valid triples through the
    existing batch_ingest pipeline (entity resolution + contradiction detection).
    Best-effort: exceptions are logged, not raised.

    Args:
        memory_id: ID of the newly stored MemoryBlock (for logging).
        content: Text content of the MemoryBlock.
        block_type: Block type tag influencing predicate bias.
        space_id: Space the memory belongs to.
        source_session: Optional session ID to tag triples with.
        db: Active async database session.

    Returns:
        Stats dict: {"triples_extracted": N, "triples_stored": M, "contradictions_resolved": K}
    """
    # Lazy import to avoid circular dependencies at module load time
    from .kg_services import TripleService

    stats = {"triples_extracted": 0, "triples_stored": 0, "contradictions_resolved": 0}

    try:
        raw_triples = await extract_triples_from_content(content, block_type)
        stats["triples_extracted"] = len(raw_triples)

        if not raw_triples:
            return stats

        session_id = source_session or f"auto_evolve:{memory_id}"

        batch = TripleBatchCreate(
            session_id=session_id,
            topic=block_type,
            triples=[
                TripleCreate(
                    subject=t["subject"],
                    predicate=t["predicate"],
                    object=t["object"],
                    topic=t.get("topic"),
                )
                for t in raw_triples
            ],
        )

        service = TripleService()
        created = await service.batch_ingest(db=db, space_id=space_id, batch=batch)
        await db.commit()

        stats["triples_stored"] = len(created)

        # Index new triples in Qdrant for vector search (best-effort)
        if created:
            try:
                from src.shared.embedding import get_embedding
                from src.shared.qdrant_client import get_qdrant_client

                client = get_qdrant_client()
                if client:
                    from qdrant_client.models import PointStruct

                    points = []
                    for triple in created:
                        text = f"{triple.subject} {triple.predicate} {triple.object}"
                        emb = await get_embedding(text, task_type="search_document")
                        if emb:
                            points.append(
                                PointStruct(
                                    id=triple.id,
                                    vector={"dense": emb},
                                    payload={
                                        "service_id": "memvault_kg",
                                        "entity_type": "triple",
                                        "space_id": space_id,
                                        "subject": triple.subject,
                                        "predicate": triple.predicate,
                                        "object": triple.object,
                                        "topic": triple.topic or "",
                                    },
                                )
                            )
                    if points:
                        from src.shared.qdrant_search import COLLECTION_NAME

                        client.upsert(
                            collection_name=COLLECTION_NAME,
                            points=points,
                        )
                        logger.debug("KG auto-evolve: indexed %d triples in Qdrant", len(points))
            except Exception:
                logger.debug("KG auto-evolve: Qdrant indexing failed (best-effort)", exc_info=True)
        # contradictions_resolved is implicit in batch_ingest (invalidated_count not exposed);
        # we report 0 here — the invalidation events are still fired by batch_ingest internally.

        logger.info(
            "KG auto-evolve: memory=%s block_type=%s extracted=%d stored=%d",
            memory_id,
            block_type,
            stats["triples_extracted"],
            stats["triples_stored"],
        )

    except Exception:
        logger.warning(
            "KG auto-evolve failed for memory=%s (best-effort, continuing)",
            memory_id,
            exc_info=True,
        )

    return stats


# ---------------------------------------------------------------------------
# Event handler registration
# ---------------------------------------------------------------------------


def register_auto_evolve_handler() -> None:
    """Subscribe to MEMORY_STORED events to trigger automatic KG evolution.

    Call this once during app startup (e.g., in the lifespan function or
    module __init__ after DB and event bus are ready).
    The handler is fire-and-forget — it never blocks the publishing coroutine.
    """

    async def _on_memory_stored(event: Event) -> None:
        data = event.data
        block_type: str = data.get("block_type", "general")
        content: str = data.get("content", "")
        memory_id: str = data.get("block_id") or data.get("id", "unknown")
        space_id: str = data.get("space_id", "")
        source_session: str | None = data.get("source_session")

        # Guard: skip noisy block types and very short content
        if block_type in _SKIP_BLOCK_TYPES:
            logger.debug(
                "KG auto-evolve: skipping block_type=%s (memory=%s)", block_type, memory_id
            )
            return
        if len(content) < _MIN_CONTENT_LENGTH:
            logger.debug(
                "KG auto-evolve: skipping short content len=%d (memory=%s)", len(content), memory_id
            )
            return
        if not space_id:
            logger.warning("KG auto-evolve: missing space_id in MEMORY_STORED event, skipping")
            return

        # Obtain a fresh DB session for this background task
        try:
            from src.shared.database import async_session_factory

            async with async_session_factory() as db:
                await auto_evolve_kg(
                    memory_id=memory_id,
                    content=content,
                    block_type=block_type,
                    space_id=space_id,
                    source_session=source_session,
                    db=db,
                )
        except Exception:
            logger.warning("KG auto-evolve session error (memory=%s)", memory_id, exc_info=True)

    event_bus.channel(MemvaultEvents.MEMORY_STORED).subscribe_handler(_on_memory_stored)
    logger.info("KG auto-evolve handler registered for %s", MemvaultEvents.MEMORY_STORED)
