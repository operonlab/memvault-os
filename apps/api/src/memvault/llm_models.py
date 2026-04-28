"""Pydantic models for LLM structured output — used as PydanticAI output_type."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ConflictResolutionOutput(BaseModel):
    """LLM output for memory conflict arbitration."""

    decision: Literal["merge", "supersede", "coexist"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = "LLM arbitration"
    merged_content: str | None = None


class DreamReflectionOutput(BaseModel):
    """LLM output for dream reflective pass."""

    health_score: float = Field(default=0.0, ge=0.0, le=1.0)
    insights: list[str] = Field(default_factory=list)
    merge_candidates: list[str] = Field(default_factory=list)
    knowledge_gaps: list[str] = Field(default_factory=list)
    suggested_attitudes: list[str] = Field(default_factory=list)
    stale_candidates: list[str] = Field(default_factory=list)


class ExtractedTriple(BaseModel):
    """A single knowledge graph triple."""

    subject: str
    predicate: str
    object: str
    topic: str | None = None


class TripleExtractionOutput(BaseModel):
    """LLM output for KG triple extraction."""

    triples: list[ExtractedTriple] = Field(default_factory=list)


class CRAGVerdictOutput(BaseModel):
    """LLM output for CRAG Layer C evaluation."""

    verdict: Literal["correct", "ambiguous", "incorrect"]


class IntentClassificationOutput(BaseModel):
    """LLM output for Tier 3 intent classification (QueryClassifyOp fallback)."""

    intent: Literal[
        "entity_lookup", "conceptual", "factual", "exploratory", "cross_domain", "unknown"
    ]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
