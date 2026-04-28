"""Memory Conflict Resolver — LLM-driven three-way arbitration.

Based on ActMem (arXiv:2603.00026) + Memory-R1 research patterns.

When new content is semantically similar to existing memory but potentially
contradictory, use LLM to determine the right action.

Three decisions:
  MERGE: Content is complementary — combine them
  SUPERSEDE: New content replaces old (temporal update / more authoritative)
  COEXIST: Different perspectives on same topic — keep both

This module is pure — it does NOT modify dedup.py. Callers in dedup or
services layer invoke ``resolve_conflict()`` when similarity is high and
semantic arbitration is desired.
"""

import json
import logging
from datetime import datetime

from pydantic_ai import Agent

from src.shared.conflict import (
    ConflictDecision,
    ConflictResult,
    simple_conflict_heuristic,
)

from .llm_config import get_litellm_model
from .llm_models import ConflictResolutionOutput

logger = logging.getLogger(__name__)


def _conflict_threshold(block_type: str = "memory") -> float:
    """Dynamic conflict similarity threshold based on block type.

    Attitudes and skills encode personal facts that should merge aggressively,
    so they use a stricter threshold (fires earlier). Knowledge blocks can
    tolerate more divergence before triggering arbitration.
    Clamped to [0.80, 0.92].
    """
    adjustments = {"attitude": 0.00, "skill": 0.02, "memory": 0, "knowledge": -0.02}
    return max(0.80, min(0.92, 0.85 + adjustments.get(block_type, 0)))


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_conflict_prompt(new_content: str, existing_content: str, block_type: str) -> str:
    """Build the system + user prompt for LLM conflict arbitration.

    The LLM receives both memory snippets and the block_type context,
    and must return a JSON object with decision, confidence, reason, and
    optionally merged_content.
    """
    system_prompt = (
        "You are a memory conflict arbitrator for a personal knowledge management system. "
        "Your task is to compare two memory fragments and decide how to handle them.\n\n"
        "Possible decisions:\n"
        '- "merge": The memories are complementary — they cover the same topic from the same angle '
        "and combining them produces a richer, non-redundant record.\n"
        '- "supersede": The new memory is an update, correction, or more authoritative version '
        "of the existing one. The existing memory should be replaced.\n"
        '- "coexist": The memories represent genuinely different perspectives, time periods, or '
        "contexts. Both are independently valuable and should be kept as separate entries.\n\n"
        "Return ONLY valid JSON with this schema (no markdown fences, no extra text):\n"
        '{"decision": "merge|supersede|coexist", '
        '"confidence": <float 0-1>, '
        '"reason": "<one sentence explanation>", '
        '"merged_content": "<combined text, only for merge decision, else null>"}'
    )

    user_prompt = (
        f"Block type: {block_type}\n\n"
        f"EXISTING memory:\n{existing_content}\n\n"
        f"NEW memory:\n{new_content}\n\n"
        "Analyze the relationship between these two memories and return the JSON decision."
    )

    return json.dumps({"system": system_prompt, "user": user_prompt})


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_conflict_agent = Agent(
    output_type=ConflictResolutionOutput,
    system_prompt=(
        "You are a memory conflict arbitrator for a personal knowledge management system. "
        "Your task is to compare two memory fragments and decide how to handle them.\n\n"
        "Possible decisions:\n"
        '- "merge": The memories are complementary — they cover the same topic from the same '
        "angle and combining them produces a richer, non-redundant record.\n"
        '- "supersede": The new memory is an update, correction, or more authoritative version '
        "of the existing one. The existing memory should be replaced.\n"
        '- "coexist": The memories represent genuinely different perspectives, time periods, or '
        "contexts. Both are independently valuable and should be kept as separate entries.\n\n"
        "When timestamps are provided, consider temporal context: a newer memory about the same "
        "topic usually supersedes an older one, especially for factual or preference content."
    ),
    retries=2,
)


async def _call_llm(
    new_content: str,
    existing_content: str,
    block_type: str,
    existing_timestamp: datetime | None = None,
    new_timestamp: datetime | None = None,
) -> ConflictResult:
    """Call oMLX local LLM and parse the conflict resolution response.

    Raises on failure — callers should catch and fall back to the heuristic.
    """
    temporal_ctx = ""
    if existing_timestamp or new_timestamp:
        parts = []
        if existing_timestamp:
            parts.append(f"EXISTING created: {existing_timestamp.isoformat()}")
        if new_timestamp:
            parts.append(f"NEW created: {new_timestamp.isoformat()}")
        temporal_ctx = "\n".join(parts) + "\n\n"

    user_message = (
        f"Block type: {block_type}\n\n"
        f"{temporal_ctx}"
        f"EXISTING memory:\n{existing_content}\n\n"
        f"NEW memory:\n{new_content}\n\n"
        "Analyze the relationship between these two memories and return the JSON decision."
    )

    result = await _conflict_agent.run(
        user_message,
        model=await get_litellm_model(),
        model_settings={"temperature": 0.1, "max_tokens": 256, "timeout": 10},
    )
    output = result.output

    decision = ConflictDecision(output.decision)
    merged_content = output.merged_content if decision == ConflictDecision.MERGE else None

    return ConflictResult(
        decision=decision,
        confidence=output.confidence,
        reason=output.reason,
        merged_content=merged_content or None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def resolve_conflict(
    new_content: str,
    existing_content: str,
    existing_block_id: str,
    block_type: str = "knowledge",
    similarity: float = 0.0,
    existing_timestamp: datetime | None = None,
    new_timestamp: datetime | None = None,
) -> ConflictResult:
    """Resolve a potential memory conflict using LLM arbitration.

    Intended to be called from the dedup layer when:
      - similarity > _conflict_threshold(block_type) (default ~0.85)
      - AND the preliminary decision is MERGE or SKIP

    Uses oMLX local LLM at port 8000 for arbitration.
    Falls back to ``_simple_conflict_heuristic`` if the LLM is unavailable
    or returns unparseable output.

    Args:
        new_content: The incoming memory content.
        existing_content: The content of the existing memory block.
        existing_block_id: ID of the existing block (for caller reference only).
        block_type: Memvault block type — knowledge | skill | attitude | general.
        similarity: Cosine similarity between the two embeddings (0-1).

    Returns:
        ConflictResult with decision, confidence, reason, and optional merged_content.
    """
    logger.debug(
        "resolve_conflict: block_id=%s type=%s sim=%.3f",
        existing_block_id,
        block_type,
        similarity,
    )

    try:
        result = await _call_llm(
            new_content, existing_content, block_type,
            existing_timestamp=existing_timestamp, new_timestamp=new_timestamp,
        )
        logger.info(
            "conflict_resolved(llm): block_id=%s decision=%s confidence=%.2f reason=%r",
            existing_block_id,
            result.decision,
            result.confidence,
            result.reason,
        )
        return result

    except Exception as exc:
        logger.warning(
            "LLM conflict resolution failed (%s) — using heuristic (block_id=%s)",
            exc,
            existing_block_id,
        )

    result = simple_conflict_heuristic(new_content, existing_content, similarity)
    logger.info(
        "conflict_resolved(heuristic): block_id=%s decision=%s reason=%r",
        existing_block_id,
        result.decision,
        result.reason,
    )
    return result


# ---------------------------------------------------------------------------
# RLM-enhanced conflict resolution
# ---------------------------------------------------------------------------

_RLM_CONFIDENCE_THRESHOLD = 0.7  # Trigger RLM when Haiku confidence below this


def _run_rlm_conflict(new_content: str, existing_content: str, block_type: str) -> ConflictResult:
    """Run RLM conflict resolution synchronously (called via asyncio.to_thread).

    RLM recursively: (1) extracts claims from both blocks, (2) analyzes
    semantic relationship, (3) produces merged content if applicable.
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
        "You are a memory conflict arbitrator. Two memory blocks may overlap, contradict, "
        "or complement each other.\n\n"
        "Your task:\n"
        "1. Extract distinct claims from EACH block\n"
        "2. Analyze the semantic relationship (complementary, contradictory, temporal update)\n"
        "3. Decide: merge / supersede / coexist\n"
        "4. If merge: produce combined content that preserves all non-redundant information\n\n"
        "Return ONLY valid JSON (no markdown fences):\n"
        '{"decision": "merge|supersede|coexist", '
        '"confidence": <float 0-1>, '
        '"reason": "<explanation>", '
        '"merged_content": "<combined text or null>"}'
    )

    context = (
        f"Block type: {block_type}\n\n"
        f"EXISTING memory:\n{existing_content}\n\n"
        f"NEW memory:\n{new_content}"
    )

    result = engine.completion(prompt=prompt, context=context)

    if result.status != "ok":
        raise RuntimeError(f"RLM returned status={result.status}")

    from src.shared.llm_json import parse_llm_json

    parsed = parse_llm_json(result.response)
    if not isinstance(parsed, dict):
        raise RuntimeError("RLM returned non-dict response")

    decision_str = str(parsed.get("decision", "coexist")).lower()
    try:
        decision = ConflictDecision(decision_str)
    except ValueError:
        decision = ConflictDecision.COEXIST

    confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    reason = str(parsed.get("reason", "RLM arbitration"))
    merged_content: str | None = (
        parsed.get("merged_content") if decision == ConflictDecision.MERGE else None
    )

    return ConflictResult(
        decision=decision,
        confidence=confidence,
        reason=f"rlm: {reason}",
        merged_content=merged_content or None,
    )


async def resolve_conflict_rlm(
    new_content: str,
    existing_content: str,
    existing_block_id: str,
    block_type: str = "knowledge",
    similarity: float = 0.0,
) -> ConflictResult:
    """RLM-enhanced conflict resolution — escalates from Haiku when confidence < 0.7.

    Flow:
      1. Run standard resolve_conflict() (Haiku via oMLX)
      2. If confidence >= 0.7, return as-is
      3. If confidence < 0.7, escalate to RLM for deeper recursive analysis
      4. On RLM failure, return the original Haiku result

    Same signature as resolve_conflict() for drop-in use.
    """
    import asyncio

    # Step 1: Run standard Haiku resolution
    haiku_result = await resolve_conflict(
        new_content=new_content,
        existing_content=existing_content,
        existing_block_id=existing_block_id,
        block_type=block_type,
        similarity=similarity,
    )

    # Step 2: Gate — only escalate on low confidence
    if haiku_result.confidence >= _RLM_CONFIDENCE_THRESHOLD:
        logger.debug(
            "resolve_conflict_rlm: haiku conf=%.2f >= %.2f, keeping (block_id=%s)",
            haiku_result.confidence,
            _RLM_CONFIDENCE_THRESHOLD,
            existing_block_id,
        )
        return haiku_result

    # Step 3: Escalate to RLM
    logger.info(
        "resolve_conflict_rlm: haiku confidence=%.2f < %.2f, escalating to RLM (block_id=%s)",
        haiku_result.confidence,
        _RLM_CONFIDENCE_THRESHOLD,
        existing_block_id,
    )

    try:
        rlm_result = await asyncio.to_thread(
            _run_rlm_conflict, new_content, existing_content, block_type
        )
        logger.info(
            "conflict_resolved(rlm): block_id=%s decision=%s confidence=%.2f reason=%r",
            existing_block_id,
            rlm_result.decision,
            rlm_result.confidence,
            rlm_result.reason,
        )
        return rlm_result

    except Exception as exc:
        logger.warning(
            "RLM conflict resolution failed — using haiku result (block_id=%s): %s",
            existing_block_id,
            exc,
        )
        return haiku_result
