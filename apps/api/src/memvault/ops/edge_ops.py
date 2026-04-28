"""Memvault Edge Signal Operators — compute multi-signal entity edge weights.

Five signals compose into composite_weight for Leiden community detection:
  S1 cooccurrence   — triple co-occurrence count
  S2 session_overlap — Jaccard coefficient of source sessions
  S3 adamic_adar     — graph common-neighbor importance
  S4 type_affinity   — entity_type match bonus
  S5 semantic_sim    — embedding cosine similarity

Pipeline shape (assembled in pipelines/edge_pipeline.py):
    EdgeCooccurrenceOp
      → EdgeAdamicAdarOp  (depends on S1 graph)
      → ParallelOp(EdgeSessionOverlapOp, EdgeTypeAffinityOp, EdgeSemanticSimilarityOp)
      → EdgeCompositeOp
      → EdgePersistOp
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ._base import MemvaultOp

logger = logging.getLogger(__name__)

# Type alias for edge signal maps
EdgeMap = dict[tuple[str, str], float | int]


def _normalize_pair(a_id: str, b_id: str) -> tuple[str, str]:
    """Ensure a_id < b_id for undirected edge normalization."""
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


# ---------------------------------------------------------------------------
# S1 — Co-occurrence count
# ---------------------------------------------------------------------------


class EdgeCooccurrenceOp(MemvaultOp):
    """Count how many valid triples link each entity pair (subject→object)."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_cooccurrence_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import Triple

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        min_cooccurrence: int = self._config.edge_min_cooccurrence

        # Count co-occurrences between canonical entity pairs
        stmt = (
            select(
                Triple.canonical_subject_id,
                Triple.canonical_object_id,
                func.count().label("cnt"),
            )
            .where(
                Triple.space_id == space_id,
                Triple.deleted_at.is_(None),
                Triple.invalid_at.is_(None),
                Triple.canonical_subject_id.isnot(None),
                Triple.canonical_object_id.isnot(None),
                Triple.canonical_subject_id != Triple.canonical_object_id,
            )
            .group_by(Triple.canonical_subject_id, Triple.canonical_object_id)
            .having(func.count() >= min_cooccurrence)
        )
        result = await db.execute(stmt)

        edge_map: EdgeMap = {}
        for row in result:
            pair = _normalize_pair(row[0], row[1])
            edge_map[pair] = edge_map.get(pair, 0) + row[2]

        ctx["edge_cooccurrence_map"] = edge_map
        logger.info("S1 cooccurrence: %d entity pairs", len(edge_map))
        return ctx


# ---------------------------------------------------------------------------
# S2 — Session overlap (Jaccard)
# ---------------------------------------------------------------------------


class EdgeSessionOverlapOp(MemvaultOp):
    """Compute Jaccard(sessions_A, sessions_B) for each entity pair."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_session_overlap_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import Triple

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]

        # Collect sessions per entity
        stmt = (
            select(
                Triple.canonical_subject_id,
                Triple.canonical_object_id,
                Triple.source_session,
            )
            .where(
                Triple.space_id == space_id,
                Triple.deleted_at.is_(None),
                Triple.invalid_at.is_(None),
                Triple.canonical_subject_id.isnot(None),
                Triple.canonical_object_id.isnot(None),
                Triple.source_session.isnot(None),
            )
        )
        result = await db.execute(stmt)

        entity_sessions: dict[str, set[str]] = defaultdict(set)
        for row in result:
            entity_sessions[row[0]].add(row[2])
            entity_sessions[row[1]].add(row[2])

        # Compute Jaccard for pairs that co-occur
        cooc_map: EdgeMap = ctx.get("edge_cooccurrence_map", {})
        edge_map: EdgeMap = {}
        for pair in cooc_map:
            a_sessions = entity_sessions.get(pair[0], set())
            b_sessions = entity_sessions.get(pair[1], set())
            union = len(a_sessions | b_sessions)
            if union > 0:
                edge_map[pair] = len(a_sessions & b_sessions) / union

        ctx["edge_session_overlap_map"] = edge_map
        logger.info("S2 session_overlap: %d pairs computed", len(edge_map))
        return ctx


# ---------------------------------------------------------------------------
# S3 — Adamic-Adar
# ---------------------------------------------------------------------------


class EdgeAdamicAdarOp(MemvaultOp):
    """Compute Adamic-Adar index: Σ 1/log(degree(z)) for common neighbors z."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id", "edge_cooccurrence_map")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_adamic_adar_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        cooc_map: EdgeMap = ctx["edge_cooccurrence_map"]

        # Build adjacency list from co-occurrence map
        neighbors: dict[str, set[str]] = defaultdict(set)
        for a_id, b_id in cooc_map:
            neighbors[a_id].add(b_id)
            neighbors[b_id].add(a_id)

        # Compute degree for each entity
        degree: dict[str, int] = {e: len(nbrs) for e, nbrs in neighbors.items()}

        # Compute Adamic-Adar for each pair
        edge_map: EdgeMap = {}
        for pair in cooc_map:
            a_nbrs = neighbors.get(pair[0], set())
            b_nbrs = neighbors.get(pair[1], set())
            common = a_nbrs & b_nbrs
            if common:
                aa_score = sum(
                    1.0 / math.log(degree[z]) for z in common if degree[z] > 1
                )
                if aa_score > 0:
                    edge_map[pair] = aa_score

        ctx["edge_adamic_adar_map"] = edge_map
        logger.info("S3 adamic_adar: %d pairs with common neighbors", len(edge_map))
        return ctx


# ---------------------------------------------------------------------------
# S4 — Type affinity
# ---------------------------------------------------------------------------

# Affinity matrix: higher score when entities share the same type
_TYPE_AFFINITY = {
    ("concept", "concept"): 0.8,
    ("tool", "tool"): 0.9,
    ("person", "person"): 0.7,
    ("org", "org"): 0.8,
    ("language", "language"): 0.9,
}
_DEFAULT_AFFINITY = 0.3  # cross-type base affinity


class EdgeTypeAffinityOp(MemvaultOp):
    """Score entity pairs by entity_type compatibility."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_type_affinity_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import EntityCanonical

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]

        # Fetch entity types
        stmt = select(EntityCanonical.id, EntityCanonical.entity_type).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        entity_types: dict[str, str] = {row[0]: row[1] for row in result}

        # Compute affinity for co-occurring pairs
        cooc_map: EdgeMap = ctx.get("edge_cooccurrence_map", {})
        edge_map: EdgeMap = {}
        for pair in cooc_map:
            type_a = entity_types.get(pair[0], "concept")
            type_b = entity_types.get(pair[1], "concept")
            key = (type_a, type_b) if type_a <= type_b else (type_b, type_a)
            edge_map[pair] = _TYPE_AFFINITY.get(key, _DEFAULT_AFFINITY)

        ctx["edge_type_affinity_map"] = edge_map
        logger.info("S4 type_affinity: %d pairs scored", len(edge_map))
        return ctx


# ---------------------------------------------------------------------------
# S5 — Semantic similarity
# ---------------------------------------------------------------------------


class EdgeSemanticSimilarityOp(MemvaultOp):
    """Compute cosine similarity between entity name embeddings via Qdrant."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_semantic_similarity_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import EntityCanonical

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]

        # Fetch entity names
        stmt = select(EntityCanonical.id, EntityCanonical.canonical_name).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        entities = {row[0]: row[1] for row in result}

        # Get embeddings via shared embedding utility
        cooc_map: EdgeMap = ctx.get("edge_cooccurrence_map", {})
        edge_map: EdgeMap = {}

        try:
            from src.shared.embedding import get_embedding

            # Collect unique entity IDs that appear in co-occurrence pairs
            entity_ids_needed: set[str] = set()
            for a_id, b_id in cooc_map:
                entity_ids_needed.add(a_id)
                entity_ids_needed.add(b_id)

            # Batch embed entity names
            embeddings: dict[str, list[float]] = {}
            for eid in entity_ids_needed:
                name = entities.get(eid)
                if name:
                    emb = await get_embedding(name, task_type="search_document")
                    if emb:
                        embeddings[eid] = emb

            # Compute pairwise cosine similarity
            for pair in cooc_map:
                emb_a = embeddings.get(pair[0])
                emb_b = embeddings.get(pair[1])
                if emb_a and emb_b:
                    dot = sum(a * b for a, b in zip(emb_a, emb_b, strict=False))
                    norm_a = math.sqrt(sum(a * a for a in emb_a))
                    norm_b = math.sqrt(sum(b * b for b in emb_b))
                    if norm_a > 0 and norm_b > 0:
                        edge_map[pair] = dot / (norm_a * norm_b)

        except ImportError:
            logger.warning("Embedding service unavailable, skipping S5")

        ctx["edge_semantic_similarity_map"] = edge_map
        logger.info("S5 semantic_similarity: %d pairs computed", len(edge_map))
        return ctx


# ---------------------------------------------------------------------------
# Composite — Weighted combination of all signals
# ---------------------------------------------------------------------------


class EdgeCompositeOp(MemvaultOp):
    """Combine five signals into a single composite_weight per edge."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return (
            "edge_cooccurrence_map",
            "edge_session_overlap_map",
            "edge_adamic_adar_map",
            "edge_type_affinity_map",
            "edge_semantic_similarity_map",
        )

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edge_composite_map",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        s1: EdgeMap = ctx["edge_cooccurrence_map"]
        s2: EdgeMap = ctx.get("edge_session_overlap_map", {})
        s3: EdgeMap = ctx.get("edge_adamic_adar_map", {})
        s4: EdgeMap = ctx.get("edge_type_affinity_map", {})
        s5: EdgeMap = ctx.get("edge_semantic_similarity_map", {})

        weights = self._config.edge_composite_weights

        # Min-max normalize S1 (integer counts) and S3 (unbounded float)
        s1_norm = _min_max_normalize(s1)
        s3_norm = _min_max_normalize(s3)
        # S2, S4, S5 are already in [0, 1]

        composite: EdgeMap = {}
        for pair in s1:
            score = (
                weights["cooccurrence"] * s1_norm.get(pair, 0.0)
                + weights["session_overlap"] * s2.get(pair, 0.0)
                + weights["adamic_adar"] * s3_norm.get(pair, 0.0)
                + weights["type_affinity"] * s4.get(pair, 0.0)
                + weights["semantic_similarity"] * s5.get(pair, 0.0)
            )
            composite[pair] = round(score, 6)

        ctx["edge_composite_map"] = composite
        logger.info(
            "Composite: %d edges, weight range [%.4f, %.4f]",
            len(composite),
            min(composite.values()) if composite else 0,
            max(composite.values()) if composite else 0,
        )
        return ctx


def _min_max_normalize(signal_map: EdgeMap) -> dict[tuple[str, str], float]:
    """Normalize values to [0, 1] using min-max scaling."""
    if not signal_map:
        return {}
    values = list(signal_map.values())
    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        return {k: 1.0 for k in signal_map}
    return {k: (v - lo) / span for k, v in signal_map.items()}


# ---------------------------------------------------------------------------
# Persist — Upsert entity_edges table
# ---------------------------------------------------------------------------


class EdgePersistOp(MemvaultOp):
    """Upsert computed edge weights into the entity_edges table."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return (
            "db",
            "space_id",
            "edge_composite_map",
            "edge_cooccurrence_map",
            "edge_session_overlap_map",
            "edge_adamic_adar_map",
            "edge_type_affinity_map",
            "edge_semantic_similarity_map",
        )

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("edges_upserted",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from uuid_utils import uuid7

        from ..kg_models import EntityEdge

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        composite: EdgeMap = ctx["edge_composite_map"]
        s1: EdgeMap = ctx["edge_cooccurrence_map"]
        s2: EdgeMap = ctx.get("edge_session_overlap_map", {})
        s3: EdgeMap = ctx.get("edge_adamic_adar_map", {})
        s4: EdgeMap = ctx.get("edge_type_affinity_map", {})
        s5: EdgeMap = ctx.get("edge_semantic_similarity_map", {})

        now = datetime.now(UTC)
        upserted = 0

        # Batch upsert using PostgreSQL INSERT ... ON CONFLICT
        batch_size = 100
        pairs = list(composite.keys())
        for i in range(0, len(pairs), batch_size):
            batch = pairs[i : i + batch_size]
            values = []
            for pair in batch:
                values.append(
                    {
                        "id": uuid7().hex,
                        "space_id": space_id,
                        "entity_a_id": pair[0],
                        "entity_b_id": pair[1],
                        "cooccurrence_count": int(s1.get(pair, 0)),
                        "session_overlap": float(s2.get(pair, 0.0)),
                        "adamic_adar": float(s3.get(pair, 0.0)),
                        "type_affinity": float(s4.get(pair, 0.0)),
                        "semantic_similarity": float(s5.get(pair, 0.0)),
                        "composite_weight": float(composite.get(pair, 0.0)),
                        "last_computed_at": now,
                    }
                )

            stmt = pg_insert(EntityEdge.__table__).values(values)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_entity_edge_pair",
                set_={
                    "cooccurrence_count": stmt.excluded.cooccurrence_count,
                    "session_overlap": stmt.excluded.session_overlap,
                    "adamic_adar": stmt.excluded.adamic_adar,
                    "type_affinity": stmt.excluded.type_affinity,
                    "semantic_similarity": stmt.excluded.semantic_similarity,
                    "composite_weight": stmt.excluded.composite_weight,
                    "last_computed_at": stmt.excluded.last_computed_at,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
            upserted += len(batch)

        await db.flush()
        ctx["edges_upserted"] = upserted
        logger.info("Persisted %d entity edges", upserted)
        return ctx
