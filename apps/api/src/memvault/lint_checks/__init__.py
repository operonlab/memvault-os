"""Knowledge Lint v2 — Task 9 (10 wiki-lint inspired checks).

Each check is a coroutine returning list[LintFinding]. Checks are intentionally
small, single-responsibility, and composable so the runner can pick subsets.

Cannibalized organising principle from claude-obsidian wiki-lint:
   report-only, never auto-fix.

Checks:
    1. orphan_blocks            — block.source_session ∉ referenced sessions
                                  (proxy for "no incoming triple")
    2. dead_triples             — triple references missing entity rows
    3. stale_claims             — wraps existing check_contradictions
    4. missing_entities         — name mentioned by ≥2 blocks but no entity row
    5. missing_cross_refs       — block text mentions entity, no triple link
    6. metadata_gaps            — required block columns blank/null
    7. empty_content            — block content < 20 chars
    8. stale_index_entries      — community/tag index points to deleted block
    9. stable_id_validity       — block_id not valid 32-hex / duplicate
   10. semantic_tiling_dedup    — block-block embedding cosine > threshold
"""

from .dead_triples import check_dead_triples
from .empty_content import check_empty_content
from .metadata_gaps import check_metadata_gaps
from .missing_cross_refs import check_missing_cross_refs
from .missing_entities import check_missing_entities
from .orphan_blocks import check_orphan_blocks
from .semantic_tiling_dedup import check_semantic_tiling_dedup
from .stable_id_validity import check_stable_id_validity
from .stale_claims import check_stale_claims
from .stale_index_entries import check_stale_index_entries

__all__ = [
    "check_dead_triples",
    "check_empty_content",
    "check_metadata_gaps",
    "check_missing_cross_refs",
    "check_missing_entities",
    "check_orphan_blocks",
    "check_semantic_tiling_dedup",
    "check_stable_id_validity",
    "check_stale_claims",
    "check_stale_index_entries",
]


# Ordered registry: (check_id, function, severity_default)
WIKI_LINT_CHECKS: list[tuple[str, object, str]] = [
    ("orphan_blocks", check_orphan_blocks, "warning"),
    ("dead_triples", check_dead_triples, "critical"),
    ("stale_claims", check_stale_claims, "warning"),
    ("missing_entities", check_missing_entities, "suggestion"),
    ("missing_cross_refs", check_missing_cross_refs, "suggestion"),
    ("metadata_gaps", check_metadata_gaps, "warning"),
    ("empty_content", check_empty_content, "warning"),
    ("stale_index_entries", check_stale_index_entries, "warning"),
    ("stable_id_validity", check_stable_id_validity, "critical"),
    ("semantic_tiling_dedup", check_semantic_tiling_dedup, "suggestion"),
]
