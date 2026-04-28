"""Memvault Reactive Operators — composable pipeline stages.

All operators implement the async Operator protocol from
core/src/shared/reactive.py (name, input_keys, output_keys, async __call__).

Base class: MemvaultOp (toggle + error isolation + timing).
"""

from ._base import MemvaultOp, PipelineMeta
from .dream_ops import (
    DreamConsolidateOp,
    DreamGatherSignalOp,
    DreamOrientOp,
    DreamPruneOp,
    DreamReflectOp,
)
from .edge_ops import (
    EdgeAdamicAdarOp,
    EdgeCompositeOp,
    EdgeCooccurrenceOp,
    EdgePersistOp,
    EdgeSemanticSimilarityOp,
    EdgeSessionOverlapOp,
    EdgeTypeAffinityOp,
)
from .lint_ops import (
    LintCommunityAnomalyOp,
    LintContradictionOp,
    LintDanglingRefOp,
    LintDataGapOp,
    LintOp,
    LintOrphanOp,
    LintPredicateContradictionOp,
    LintStaleOp,
    LintTemporalStalenessOp,
    MergeFindingsOp,
)
from .review_ops import ReviewAutoApproveOp
from .surprise_ops import (
    MergeSurprisesOp,
    SurpriseCrossCommunityOp,
    SurpriseIndirectStrongOp,
    SurpriseKnowledgeGapOp,
)

__all__ = [
    "DreamConsolidateOp",
    "DreamGatherSignalOp",
    "DreamOrientOp",
    "DreamPruneOp",
    "DreamReflectOp",
    "EdgeAdamicAdarOp",
    "EdgeCompositeOp",
    "EdgeCooccurrenceOp",
    "EdgePersistOp",
    "EdgeSemanticSimilarityOp",
    "EdgeSessionOverlapOp",
    "EdgeTypeAffinityOp",
    "LintCommunityAnomalyOp",
    "LintContradictionOp",
    "LintDanglingRefOp",
    "LintDataGapOp",
    "LintOp",
    "LintOrphanOp",
    "LintPredicateContradictionOp",
    "LintStaleOp",
    "LintTemporalStalenessOp",
    "MemvaultOp",
    "MergeFindingsOp",
    "MergeSurprisesOp",
    "PipelineMeta",
    "ReviewAutoApproveOp",
    "SurpriseCrossCommunityOp",
    "SurpriseIndirectStrongOp",
    "SurpriseKnowledgeGapOp",
]
