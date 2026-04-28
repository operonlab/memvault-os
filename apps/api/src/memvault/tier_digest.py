"""Hierarchical summarization on tier transitions for memvault.

Inspired by Graphiti's hierarchical summarization and memory-lancedb-pro's
tier management (P4).

Digest rules:
  Warm → Cold  : digest stored as ``digest:<text>`` tag on BlockArchive
  Cold → Frozen: digest stored in BlockFrozen.summary
  Upward / Hot→Warm: no digest
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared.tier_manager import TierTransition

from .llm_config import get_litellm_model
from .models import BlockArchive, BlockFrozen, MemoryBlock

logger = logging.getLogger(__name__)

_DIGEST_MAX_CHARS = 400
_DIGEST_TAG_PREFIX = "digest:"
_DIGEST_TIERS = {"cold", "frozen"}
_UPWARD_PAIRS = {("cold", "warm"), ("warm", "hot"), ("frozen", "cold")}

_digest_agent = Agent(
    output_type=str,
    system_prompt=(
        "You are a memory compressor. Given a memory block, compress it into "
        "1-2 sentences preserving key facts. Return ONLY the compressed text."
    ),
    retries=1,
)


async def generate_digest(content: str, block_type: str, tags: list[str]) -> str:
    """Generate a 1-2 sentence digest via local LLM (oMLX).

    Falls back to simple truncation when the LLM is unavailable or times out.
    """
    tag_hint = ", ".join(tags[:5]) if tags else "none"
    user_msg = f"Block type: {block_type}\nTags: {tag_hint}\n\n{content}"
    try:
        result = await _digest_agent.run(
            user_msg,
            model=await get_litellm_model(),
            model_settings={"temperature": 0.2, "max_tokens": 120, "timeout": 5},
        )
        digest = result.output.strip()
        logger.debug("tier_digest: LLM digest generated (%d chars)", len(digest))
        return digest
    except Exception as exc:
        logger.warning("tier_digest: LLM unavailable (%s), using truncation fallback", exc)
        return content[:_DIGEST_MAX_CHARS].rstrip() + (
            "…" if len(content) > _DIGEST_MAX_CHARS else ""
        )


async def process_tier_transition(
    transition: TierTransition,
    db: AsyncSession,
) -> dict[str, Any]:
    """Process a single TierTransition; generate and persist a digest for downward moves.

    Returns a status dict with keys: memory_id, from_tier, to_tier, action, digest.
    """
    status: dict[str, Any] = {
        "memory_id": transition.memory_id,
        "from_tier": transition.from_tier,
        "to_tier": transition.to_tier,
        "action": "skipped",
        "digest": None,
    }

    pair = (transition.from_tier, transition.to_tier)

    if pair in _UPWARD_PAIRS:
        logger.debug(
            "tier_digest: upward %s→%s for %s, skipping",
            transition.from_tier,
            transition.to_tier,
            transition.memory_id,
        )
        status["action"] = "upward_skip"
        return status

    if transition.to_tier not in _DIGEST_TIERS:
        status["action"] = "no_digest_needed"
        return status

    # Fetch source MemoryBlock (still lives in hot/warm table at call time)
    row = await db.execute(select(MemoryBlock).where(MemoryBlock.id == transition.memory_id))
    block = row.scalars().first()

    if block is None:
        logger.warning("tier_digest: MemoryBlock %s not found", transition.memory_id)
        status["action"] = "source_not_found"
        return status

    digest = await generate_digest(block.content, block.block_type, list(block.tags or []))

    if transition.to_tier == "cold":
        await _attach_digest_to_archive(transition.memory_id, digest, db)
        status["action"] = "digest_tagged_archive"
    else:  # frozen
        await _attach_digest_to_frozen(transition.memory_id, digest, db)
        status["action"] = "digest_set_frozen_summary"

    status["digest"] = digest
    logger.info(
        "tier_digest: %s→%s for %s action=%s",
        transition.from_tier,
        transition.to_tier,
        transition.memory_id,
        status["action"],
    )
    return status


async def batch_process_transitions(
    transitions: list[TierTransition],
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Process a list of TierTransitions best-effort; failures do not abort the batch."""
    results: list[dict[str, Any]] = []
    for transition in transitions:
        try:
            result = await process_tier_transition(transition, db)
        except Exception as exc:
            logger.error(
                "tier_digest: error for %s (%s→%s): %s",
                transition.memory_id,
                transition.from_tier,
                transition.to_tier,
                exc,
                exc_info=True,
            )
            result = {
                "memory_id": transition.memory_id,
                "from_tier": transition.from_tier,
                "to_tier": transition.to_tier,
                "action": "error",
                "digest": None,
                "error": str(exc),
            }
        results.append(result)
    return results


async def _attach_digest_to_archive(memory_id: str, digest: str, db: AsyncSession) -> None:
    """Append a ``digest:<text>`` tag to the matching BlockArchive record."""
    row = await db.execute(select(BlockArchive).where(BlockArchive.id == memory_id))
    archive = row.scalars().first()
    if archive is None:
        logger.debug("tier_digest: BlockArchive %s not yet written; tag deferred", memory_id)
        return
    existing = [t for t in (archive.tags or []) if not t.startswith(_DIGEST_TAG_PREFIX)]
    existing.append(f"{_DIGEST_TAG_PREFIX}{digest}")
    archive.tags = existing
    db.add(archive)


async def _attach_digest_to_frozen(memory_id: str, digest: str, db: AsyncSession) -> None:
    """Set BlockFrozen.summary for the matching record."""
    row = await db.execute(select(BlockFrozen).where(BlockFrozen.id == memory_id))
    frozen = row.scalars().first()
    if frozen is None:
        logger.debug("tier_digest: BlockFrozen %s not yet written; summary deferred", memory_id)
        return
    frozen.summary = digest
    db.add(frozen)
