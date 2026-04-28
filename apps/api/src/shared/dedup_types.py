"""Dedup types and category rules — shared vocabulary for deduplication decisions.

**Dedup vs Conflict** — these are fundamentally different problems:
- Dedup: same content appears multiple times → SKIP (don't create) or MERGE (combine)
- Conflict: same topic, different claims → SUPERSEDE (newer wins) or COEXIST (both valid)

Dedup decisions (this module): SKIP / MERGE / CREATE / SUPERSEDE
Conflict decisions (conflict.py): MERGE / SUPERSEDE / COEXIST

Lint naming convention:
- Dedup checks: ``*_dedup`` (e.g., attitude_dedup)
- Conflict checks: ``*_contradictions`` (e.g., predicate_contradictions)

Extracted from memvault dedup.py. The DB-coupled check_duplicate() and
find_similar_blocks() remain in memvault; only pure types, enums,
rules, and threshold functions are shared.
"""

from dataclasses import dataclass, field
from enum import Enum

# Static threshold constants
DEDUP_SIMILARITY_THRESHOLD = 0.88
CONTENT_OVERLAP_RATIO = 0.7


def dedup_similarity_threshold(category: str = "knowledge") -> float:
    """Dynamic dedup threshold based on block category. Clamped to [0.70, 0.92]."""
    adjustments = {"attitude": -0.13, "skill": 0.04, "knowledge": 0.0, "general": 0.0}
    return max(0.70, min(0.92, 0.88 + adjustments.get(category, 0.0)))


def conflict_dedup_threshold(block_type: str = "general") -> float:
    """Dynamic LLM arbitration trigger threshold. Clamped to [0.80, 0.92]."""
    adjustments = {"attitude": 0.03, "skill": 0.02, "memory": 0, "knowledge": -0.02}
    return max(0.80, min(0.92, 0.85 + adjustments.get(block_type, 0)))


class DedupDecision(Enum):
    CREATE = "create"
    SKIP = "skip"
    MERGE = "merge"
    SUPERSEDE = "supersede"


class DedupBehavior(Enum):
    ALWAYS_MERGE = "always_merge"
    MERGE_IF_SIMILAR = "merge_if_similar"
    APPEND_ONLY = "append_only"
    SUPERSEDE = "supersede"


@dataclass
class CategoryDedupRule:
    behavior: DedupBehavior
    threshold: float


CATEGORY_DEDUP_RULES: dict[str, CategoryDedupRule] = {
    "knowledge": CategoryDedupRule(
        behavior=DedupBehavior.MERGE_IF_SIMILAR,
        threshold=0.88,
    ),
    "attitude": CategoryDedupRule(
        behavior=DedupBehavior.MERGE_IF_SIMILAR,
        threshold=0.82,
    ),
    "skill": CategoryDedupRule(
        behavior=DedupBehavior.APPEND_ONLY,
        threshold=0.88,
    ),
    "general": CategoryDedupRule(
        behavior=DedupBehavior.MERGE_IF_SIMILAR,
        threshold=0.88,
    ),
}

_DEFAULT_DEDUP_RULE = CategoryDedupRule(
    behavior=DedupBehavior.MERGE_IF_SIMILAR,
    threshold=DEDUP_SIMILARITY_THRESHOLD,
)


def get_dedup_rule(block_type: str | None) -> CategoryDedupRule:
    """Look up per-category dedup rule. Falls back to MERGE_IF_SIMILAR."""
    if block_type is None:
        return _DEFAULT_DEDUP_RULE
    return CATEGORY_DEDUP_RULES.get(block_type, _DEFAULT_DEDUP_RULE)


@dataclass
class DedupResult:
    decision: DedupDecision
    existing_block_id: str | None = None
    similarity: float = 0.0
    reason: str = ""
    block_type: str | None = field(default=None)
