"""Shared types for Qdrant hybrid search across all modules."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class IndexDocument:
    """A document to be indexed in Qdrant."""

    service_id: str  # "memvault", "intelflow", "taskflow", etc.
    entity_id: str  # UUID v7 of the source record
    entity_type: str  # "block", "report", "task", "capture", etc.
    space_id: str  # tenant isolation
    content: str  # text to embed + tokenize
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """A single hybrid search result from Qdrant."""

    entity_id: str
    service_id: str
    entity_type: str
    score: float
    content_preview: str  # first 200 chars
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None  # dense vector, populated when with_vectors=True


@dataclass
class SearchConfig:
    """Configuration for hybrid search."""

    top_k: int = 10
    score_threshold: float = 0.0
    sparse_weight: float = 0.5  # BM25 weight in RRF
    dense_weight: float = 0.5  # Dense embedding weight in RRF
    use_sparse: bool = True
    use_dense: bool = True
    service_ids: list[str] | None = None  # filter by service(s)
    tag_filter: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


@dataclass
class SearchMetadata:
    """Metadata about the search operation."""

    backend: str = "qdrant"  # "qdrant" or "ilike_fallback"
    sparse_used: bool = False
    dense_used: bool = False
    total_candidates: int = 0
    query_time_ms: float = 0.0
    collection: str = ""
