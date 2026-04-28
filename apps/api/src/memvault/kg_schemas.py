"""Memvault KG Pydantic schemas — request/response types for Knowledge Graph."""

from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, Field

from src.shared.schemas import SpaceScopedResponse


# ======================== Retrieval Mode (LightRAG-inspired) ========================


class RetrievalMode(StrEnum):
    """Retrieval mode for cascade recall — LightRAG-inspired dual-layer switching."""

    LOCAL = "local"  # Entity-centric: PPR + L0 triples + blocks
    GLOBAL = "global"  # Community-level: L2 summaries + L1 communities
    HYBRID = "hybrid"  # Both (current default)
    AUTO = "auto"  # Router decides based on intent

# ======================== Triple ========================


class TripleCreate(BaseModel):
    subject: str = Field(
        ...,
        max_length=500,
        validation_alias=AliasChoices("subject", "s"),
    )
    predicate: str = Field(
        ...,
        max_length=100,
        validation_alias=AliasChoices("predicate", "p"),
    )
    object: str = Field(
        validation_alias=AliasChoices("object", "o"),
    )
    source_session: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_session", "session_id"),
    )
    timestamp: datetime | None = None
    topic: str | None = Field(default=None, max_length=500)


class TripleBatchCreate(BaseModel):
    """Batch ingest from extract-triples pipeline."""

    session_id: str
    topic: str | None = None
    timestamp: datetime | None = None
    triples: list[TripleCreate]


class TripleResponse(SpaceScopedResponse):
    subject: str
    predicate: str
    object: str
    source_session: str | None = None
    timestamp: datetime | None = None
    topic: str | None = None
    display_zh: str | None = None
    # Edge invalidation
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    invalidated_by: str | None = None
    invalidation_reason: str | None = None
    # Entity resolution
    canonical_subject_id: str | None = None
    canonical_object_id: str | None = None
    # embedding intentionally excluded from response


# ======================== Community ========================


class CommunityResponse(SpaceScopedResponse):
    name: str
    resolution_level: int
    size: int
    top_entities: list[str] = []
    top_predicates: list[str] = []
    summary: str | None = None
    description_zh: str | None = None
    parent_community_id: str | None = None
    modularity_score: float | None = None
    generation_batch: str | None = None


class CommunityDetail(CommunityResponse):
    """Community with its member triples and children communities."""

    triples: list[TripleResponse] = []
    children: list["CommunityResponse"] = []


# ======================== CommunitySummary ========================


class CommunitySummaryResponse(SpaceScopedResponse):
    community_id: str
    summary: str
    key_findings: list[str] = []
    representative_triples: list[str] = []
    evidence_count: int | None = None
    tags: list[str] = []
    llm_model: str | None = None
    staleness_score: float | None = None  # 0.0=fresh, 1.0=very stale

    @property
    def is_stale(self) -> bool:
        """Summary is considered stale if updated_at > 30 days ago."""
        from datetime import UTC, datetime, timedelta

        if not self.updated_at:
            return False
        return (datetime.now(UTC) - self.updated_at) > timedelta(days=30)



# ======================== Pipeline Regenerate ========================


class CommunityRegenerateRequest(BaseModel):
    """Payload from community_pipeline.py — atomic community replacement."""

    communities: list[dict]
    generated_at: str | None = None
    resolution_level: int | None = None


class CommunitySummaryRegenerateRequest(BaseModel):
    """Payload from community_summary_pipeline.py — atomic summary replacement."""

    summaries: list[dict]
    generated_at: str | None = None


# ======================== Cascade Recall ========================


class CascadeRecallResult(BaseModel):
    """Multi-layer recall result."""

    summaries: list[CommunitySummaryResponse] = []  # L2
    communities: list[CommunityResponse] = []  # L1
    triples: list[TripleResponse] = []  # L0
    blocks: list = []  # existing blocks (import MemoryBlockResponse if needed)
    layers_searched: list[str] = []  # which layers returned results
    # Phase 2: Query routing metadata
    routing_intent: str | None = None
    routing_confidence: float | None = None
    # LightRAG retrieval mode
    retrieval_mode: str | None = None
    # Phase 3: CRAG evaluation metadata
    confidence_score: float | None = None
    evaluation_verdict: str | None = None
    evaluation_metadata: dict | None = None


# ======================== Triple Invalidation ========================


class TripleInvalidateRequest(BaseModel):
    """Manual invalidation of a triple."""

    reason: str = Field(default="manual", max_length=50)
    replacement_triple_id: str | None = None


# ======================== Entity Resolution ========================


class EntityCanonicalResponse(SpaceScopedResponse):
    canonical_name: str
    aliases: list[str] = []
    entity_type: str = "concept"
    merge_count: int = 1


class EntityMergeRequest(BaseModel):
    primary_id: str
    secondary_id: str


class EntityMergeResult(BaseModel):
    merged_id: str
    canonical_name: str
    aliases: list[str]
    triples_updated: int


class EntityResolutionStats(BaseModel):
    total_entities: int
    total_aliases: int
    avg_merge_count: float
    unresolved_triples: int


# ======================== Graph Traversal ========================


class GraphNode(BaseModel):
    """A unique entity discovered during traversal."""

    id: str  # Entity name string
    label: str
    depth: int
    triple_count: int = 0


class GraphEdge(BaseModel):
    """A triple represented as a directed edge."""

    id: str  # Triple DB id
    source: str  # subject
    target: str  # object
    predicate: str
    depth: int


class GraphTraversalResult(BaseModel):
    """Graph structure for visualization."""

    seed_entity: str
    direction: str  # outgoing | incoming | both
    max_depth: int
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    total_triples_traversed: int = 0
    truncated: bool = False


# ======================== Entity Edge (Multi-Signal) ========================


class EntityEdgeResponse(SpaceScopedResponse):
    """Weighted edge between two canonical entities."""

    entity_a_id: str
    entity_b_id: str
    entity_a_name: str = ""
    entity_b_name: str = ""
    # Five signals
    cooccurrence_count: int = 0
    session_overlap: float = 0.0
    adamic_adar: float = 0.0
    type_affinity: float = 0.0
    semantic_similarity: float = 0.0
    # Composite
    composite_weight: float = 0.0
    last_computed_at: datetime | None = None


class EdgeRecomputeRequest(BaseModel):
    """Request to recompute specific edge signals."""

    signals: list[str] | None = None  # None = all signals


# ======================== Surprise Connections ========================


class SurpriseConnection(BaseModel):
    """An unexpected or noteworthy connection discovered via multi-signal analysis."""

    entity_a: str
    entity_b: str
    entity_a_id: str = ""
    entity_b_id: str = ""
    strategy: str  # indirect_strong | cross_community | knowledge_gap
    signal_breakdown: dict[str, float] = {}
    explanation: str = ""
    community_a: str | None = None
    community_b: str | None = None


# ======================== Review Queue ========================


class ReviewItem(BaseModel):
    """A pending item awaiting human review."""

    id: str
    item_type: str  # block | triple
    content_preview: str
    invalidation_reason: str | None = None
    superseded_by: str | None = None
    replacement_preview: str | None = None
    created_at: datetime | None = None
    invalidated_at: datetime | None = None


class ReviewAction(BaseModel):
    """Action on a review queue item."""

    action: str  # approve | reject | defer
    note: str | None = None


# ======================== Lint ========================


class LintFindingResponse(BaseModel):
    """A single lint finding."""

    check: str
    severity: str
    entity_id: str
    entity_type: str
    message: str
    suggested_action: str
    metadata: dict = {}


class LintReportResponse(BaseModel):
    """Full lint report."""

    space_id: str
    checks_run: list[str]
    findings: list[LintFindingResponse]
    summary: dict[str, int]
    run_duration_ms: float
    run_at: str
    remediations_applied: int = 0
