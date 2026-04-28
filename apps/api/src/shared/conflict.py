"""Conflict resolution types and heuristic — shared by memvault, docvault.

**Conflict vs Dedup** — these are fundamentally different problems:
- Conflict: same topic, different claims → MERGE / SUPERSEDE / COEXIST
- Dedup: same content, multiple copies → handled by dedup_types.py

Conflict arises when two pieces of knowledge disagree (e.g., belief evolution,
factual contradiction). Resolution requires judgment (LLM or heuristic).

Extracted from memvault conflict_resolver.py. The LLM-based resolution
and RLM escalation remain in memvault; only the pure types and fallback
heuristic are shared.
"""

from dataclasses import dataclass
from enum import StrEnum

CONFLICT_SIMILARITY_THRESHOLD = 0.85
HIGH_SIMILARITY_THRESHOLD = 0.95


class ConflictDecision(StrEnum):
    MERGE = "merge"
    SUPERSEDE = "supersede"
    COEXIST = "coexist"


@dataclass
class ConflictResult:
    decision: ConflictDecision
    confidence: float
    reason: str
    merged_content: str | None = None


def simple_conflict_heuristic(
    new_content: str,
    existing_content: str,
    similarity: float,
) -> ConflictResult:
    """Fallback heuristic when LLM is unavailable.

    Logic:
    - Very high similarity (>0.95) + high word overlap → MERGE
    - Otherwise → COEXIST (different perspectives)
    """
    words_new = set(new_content.lower().split())
    words_existing = set(existing_content.lower().split())

    if not words_new or not words_existing:
        return ConflictResult(
            decision=ConflictDecision.COEXIST,
            confidence=0.5,
            reason="empty_content_fallback",
        )

    intersection = words_new & words_existing
    union = words_new | words_existing
    overlap = len(intersection) / len(union)

    if similarity > HIGH_SIMILARITY_THRESHOLD and overlap > 0.7:
        return ConflictResult(
            decision=ConflictDecision.MERGE,
            confidence=0.7,
            reason=f"heuristic_high_overlap (sim={similarity:.3f}, overlap={overlap:.2f})",
        )

    return ConflictResult(
        decision=ConflictDecision.COEXIST,
        confidence=0.6,
        reason=f"heuristic_coexist (sim={similarity:.3f}, overlap={overlap:.2f})",
    )
