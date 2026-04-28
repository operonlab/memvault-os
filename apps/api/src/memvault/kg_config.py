"""Memvault KG constants — predicate vocabulary (from kg-ops) + memvault-specific types."""

from kg_ops import (
    PREDICATE_ALIASES,
    PREDICATE_VOCABULARY,
    VALID_PREDICATES,
    normalize_predicate,
)

# Re-export for backward compatibility (consumers import from here)
__all__ = [
    "ATTITUDE_CATEGORIES",
    "ATTITUDE_OPERATIONS",
    "BLOCK_TYPES",
    "PREDICATE_ALIASES",
    "PREDICATE_VOCABULARY",
    "PROTECTED_BLOCK_TYPES",
    "SKILL_OUTCOMES",
    "VALID_PREDICATES",
    "normalize_predicate",
]

# ---------------------------------------------------------------------------
# Block Types — canonical vocabulary for MemoryBlock.block_type
# ---------------------------------------------------------------------------
BLOCK_TYPES = frozenset({"knowledge", "skill", "attitude", "general"})

# Block types protected from GRC curation even with low confidence scores.
PROTECTED_BLOCK_TYPES = frozenset({"lesson", "correction", "decision", "rule"})

ATTITUDE_CATEGORIES = [
    "workflow",
    "tool_behavior",
    "config",
    "architecture",
    "preference",
    "testing_philosophy",
    "autonomy_level",
    "safety",
    "design_preference",
    "system_feedback",
]

SKILL_OUTCOMES = ["success", "failure", "partial", "unknown"]
ATTITUDE_OPERATIONS = ["ADD", "UPDATE", "NOOP"]
