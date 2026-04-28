"""Predicate vocabulary and normalization.

Extracted from memvault/kg_config.py. Extended with register_predicates()
for domain-specific predicate expansion (e.g., docvault document relations).
"""

from __future__ import annotations

# 20 predicates in 7 categories (from V1 triple extraction)
PREDICATE_VOCABULARY: dict[str, list[str]] = {
    "dependency": ["uses", "requires", "depends_on"],
    "config": ["configured_with", "format_is", "default_is"],
    "causation": ["causes", "prevents", "fixes", "enables"],
    "normative": ["should", "should_NOT"],
    "pattern": ["pattern_is", "flow_is", "implemented_as"],
    "decision": ["chosen_over", "reason_for"],
    "effect": ["improves", "degrades"],
    "mapping": ["maps_to"],
}

# Flatten for validation
VALID_PREDICATES: set[str] = {p for preds in PREDICATE_VOCABULARY.values() for p in preds}

# 40+ alias → canonical predicate mapping
PREDICATE_ALIASES: dict[str, str] = {
    "depends on": "depends_on",
    "needs": "requires",
    "need": "requires",
    "is configured with": "configured_with",
    "configured with": "configured_with",
    "configures": "configured_with",
    "config_is": "configured_with",
    "has_format": "format_is",
    "defaults_to": "default_is",
    "default": "default_is",
    "caused_by": "causes",
    "leads_to": "causes",
    "triggers": "causes",
    "avoids": "prevents",
    "blocks": "prevents",
    "solves": "fixes",
    "fixed_by": "fixes",
    "resolves": "fixes",
    "allows": "enables",
    "unlocks": "enables",
    "supports": "enables",
    "must": "should",
    "should_use": "should",
    "recommended": "should",
    "must_not": "should_NOT",
    "should_not": "should_NOT",
    "avoid": "should_NOT",
    "do_not": "should_NOT",
    "dont": "should_NOT",
    "implemented_by": "implemented_as",
    "built_with": "implemented_as",
    "runs_on": "implemented_as",
    "works_as": "pattern_is",
    "follows": "pattern_is",
    "architecture_is": "pattern_is",
    "pipeline_is": "flow_is",
    "workflow_is": "flow_is",
    "preferred_over": "chosen_over",
    "replaced_by": "chosen_over",
    "selected_over": "chosen_over",
    "because": "reason_for",
    "motivation": "reason_for",
    "speeds_up": "improves",
    "optimizes": "improves",
    "enhances": "improves",
    "slows_down": "degrades",
    "hurts": "degrades",
    "worsens": "degrades",
    "equivalent_to": "maps_to",
    "corresponds_to": "maps_to",
    "translates_to": "maps_to",
}


def normalize_predicate(predicate: str) -> str:
    """Normalize a predicate to canonical form using alias mapping."""
    p = predicate.strip().lower()
    if p in VALID_PREDICATES:
        return p
    return PREDICATE_ALIASES.get(p, p)


def register_predicates(category: str, predicates: list[str]) -> None:
    """Register domain-specific predicates at runtime.

    Example:
        register_predicates("document", ["defines", "references", "summarizes"])
    """
    if category not in PREDICATE_VOCABULARY:
        PREDICATE_VOCABULARY[category] = []
    for p in predicates:
        if p not in VALID_PREDICATES:
            PREDICATE_VOCABULARY[category].append(p)
            VALID_PREDICATES.add(p)
