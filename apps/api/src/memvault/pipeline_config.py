"""Unified pipeline configuration for all memvault Reactive Operator pipelines.

Controls per-stage enable/disable toggles and pipeline-specific parameters.
Follows the ScoringConfig.stages_enabled pattern from scoring_pipeline.py.

injection_guard is deliberately absent — security boundaries must NOT be toggleable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class MemvaultPipelineConfig:
    """Configuration for memvault pipeline stages.

    All stages default to True unless explicitly marked opt-in (False).
    """

    stages_enabled: dict[str, bool] = field(
        default_factory=lambda: {
            # Dream phases
            "dream.orient": True,
            "dream.gather_signal": True,
            "dream.reflect": True,
            "dream.consolidate": True,
            "dream.prune": True,
            # Dream consolidate sub-steps (reserved for future DreamConsolidateOp decomposition;
            # currently NOT checked by any is_enabled() call — DreamConsolidateOp is monolithic)
            "dream.consolidate.contradictions": True,
            "dream.consolidate.dedup": True,
            "dream.consolidate.normalize": True,
            # Lint checks
            "lint.contradictions": True,
            "lint.stale": True,
            "lint.orphan_entities": True,
            "lint.dangling_refs": True,
            "lint.community_anomalies": True,
            "lint.data_gaps": True,
            "lint.predicate_contradictions": True,
            "lint.temporal_staleness": True,
            "lint.semantic_contradictions": False,  # opt-in; no LintOp yet (placeholder)
            # Query pipeline
            "query.route": True,
            "query.expand": True,
            "query.rerank": True,
            "query.crag_eval": True,
            # CRAG layers (reserved; controlled by `evaluate` param, not these toggles)
            "crag.layer_a": True,
            "crag.layer_b": True,
            "crag.layer_c": False,  # opt-in (Haiku LLM)
            "crag.layer_d": False,  # opt-in (RLM escalation)
            # Edge signal pipeline
            "edge.cooccurrence": True,
            "edge.session_overlap": True,
            "edge.adamic_adar": True,
            "edge.type_affinity": True,
            "edge.semantic_similarity": True,
            "edge.composite": True,
            "edge.persist": True,
            # Surprise discovery
            "surprise.indirect_strong": True,
            "surprise.cross_community": True,
            "surprise.knowledge_gap": True,
            "surprise.merge": True,
            # Review auto-approve
            "review.auto_approve": True,
        }
    )

    # Dream pipeline parameters
    dream_dual_gate_hours: int = 24
    dream_dual_gate_sessions: int = 5
    dream_max_contradictions: int = 30
    dream_max_merges: int = 50
    dream_dedup_threshold: float = 0.92

    # Lint pipeline parameters
    lint_contradiction_sample_size: int = 100
    lint_stale_days: int = 90
    lint_semantic_sample_size: int = 50
    lint_max_llm_calls: int = 20

    # Curate parameters (used by DreamPruneOp)
    curate_confidence_threshold: float = 0.15
    curate_max_soft_delete: int = 50

    # Edge signal pipeline parameters
    edge_composite_weights: dict = field(
        default_factory=lambda: {
            "cooccurrence": 0.30,
            "session_overlap": 0.20,
            "adamic_adar": 0.20,
            "type_affinity": 0.10,
            "semantic_similarity": 0.20,
        }
    )
    edge_min_cooccurrence: int = 1  # minimum co-occurrences to create an edge

    # Surprise discovery parameters
    surprise_limit: int = 10  # max results per strategy

    # Review auto-approve parameters
    review_auto_approve_days: int = 14  # auto-approve pending items older than N days

    def is_enabled(self, stage_name: str) -> bool:
        """Check if a stage is enabled. Unknown stages default to True."""
        return self.stages_enabled.get(stage_name, True)

    @classmethod
    def from_env(cls) -> MemvaultPipelineConfig:
        """Load config with overrides from environment variables.

        MEMVAULT_STAGES_DISABLED: comma-separated stage names to disable.
            e.g. "dream.reflect,lint.semantic_contradictions"

        MEMVAULT_STAGES_ENABLED: comma-separated stage names to enable.
            e.g. "lint.semantic_contradictions,crag.layer_c"
        """
        config = cls()
        disabled = os.environ.get("MEMVAULT_STAGES_DISABLED", "")
        if disabled:
            for stage in disabled.split(","):
                stage = stage.strip()
                if stage:
                    config.stages_enabled[stage] = False

        enabled = os.environ.get("MEMVAULT_STAGES_ENABLED", "")
        if enabled:
            for stage in enabled.split(","):
                stage = stage.strip()
                if stage:
                    config.stages_enabled[stage] = True

        return config
