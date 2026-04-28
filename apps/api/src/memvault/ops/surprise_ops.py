"""Memvault Surprise Discovery Operators — find unexpected knowledge connections.

Three strategies for discovering non-obvious relationships:
  indirect_strong   — high Adamic-Adar but low co-occurrence (hidden bridges)
  cross_community   — strong edges between different L1 communities (cross-domain)
  knowledge_gap     — high type affinity but low composite weight (expected but missing)

Pipeline shape (assembled in pipelines/surprise_pipeline.py):
    ParallelOp(SurpriseIndirectStrongOp, SurpriseCrossCommunityOp, SurpriseKnowledgeGapOp)
      → MergeSurprisesOp
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._base import MemvaultOp

logger = logging.getLogger(__name__)


class SurpriseIndirectStrongOp(MemvaultOp):
    """High Adamic-Adar + low co-occurrence = hidden indirect connections."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("surprises_indirect_strong",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import EntityCanonical, EntityEdge

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        limit: int = self._config.surprise_limit

        # Find edges with high adamic_adar but very low cooccurrence
        # P75 of adamic_adar as threshold (computed inline)
        p75_subq = (
            select(func.percentile_cont(0.75).within_group(EntityEdge.adamic_adar))
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.adamic_adar > 0,
            )
            .scalar_subquery()
        )

        ea = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("ea")
        eb = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("eb")

        stmt = (
            select(
                EntityEdge,
                ea.c.canonical_name.label("name_a"),
                eb.c.canonical_name.label("name_b"),
            )
            .join(ea, EntityEdge.entity_a_id == ea.c.id)
            .join(eb, EntityEdge.entity_b_id == eb.c.id)
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.adamic_adar >= p75_subq,
                EntityEdge.cooccurrence_count <= 1,
            )
            .order_by(EntityEdge.adamic_adar.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)

        surprises = []
        for row in result:
            edge = row[0]
            surprises.append(
                {
                    "entity_a": row.name_a,
                    "entity_b": row.name_b,
                    "entity_a_id": edge.entity_a_id,
                    "entity_b_id": edge.entity_b_id,
                    "strategy": "indirect_strong",
                    "signal_breakdown": {
                        "cooccurrence": edge.cooccurrence_count,
                        "session_overlap": edge.session_overlap,
                        "adamic_adar": edge.adamic_adar,
                        "type_affinity": edge.type_affinity,
                        "semantic_similarity": edge.semantic_similarity,
                    },
                    "explanation": (
                        f"{row.name_a} and {row.name_b} share many common neighbors "
                        f"(AA={edge.adamic_adar:.2f}) but rarely appear together directly"
                    ),
                }
            )

        ctx["surprises_indirect_strong"] = surprises
        logger.info("Surprise indirect_strong: %d found", len(surprises))
        return ctx


class SurpriseCrossCommunityOp(MemvaultOp):
    """Strong edges between entities in different L1 communities."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("surprises_cross_community",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import Community, CommunityTriple, EntityCanonical, EntityEdge, Triple

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        limit: int = self._config.surprise_limit

        # P50 of composite_weight as threshold
        p50_subq = (
            select(
                func.percentile_cont(0.5).within_group(EntityEdge.composite_weight)
            )
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.composite_weight > 0,
            )
            .scalar_subquery()
        )

        # Build entity → community_name mapping via SQL
        # Get L1 communities (resolution_level=1) entity memberships
        entity_community_subq = (
            select(
                Triple.canonical_subject_id.label("entity_id"),
                Community.name.label("community_name"),
                Community.id.label("community_id"),
            )
            .join(CommunityTriple, CommunityTriple.triple_id == Triple.id)
            .join(Community, Community.id == CommunityTriple.community_id)
            .where(
                Community.resolution_level == 1,
                Community.space_id == space_id,
                Community.deleted_at.is_(None),
                Triple.deleted_at.is_(None),
                Triple.invalid_at.is_(None),
            )
            .distinct()
            .subquery("ec")
        )

        # We'll do a simpler approach: fetch edges above P50, then check community membership
        stmt = (
            select(EntityEdge)
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.composite_weight >= p50_subq,
            )
            .order_by(EntityEdge.composite_weight.desc())
            .limit(limit * 3)  # fetch extra, filter in Python
        )
        result = await db.execute(stmt)
        edges = list(result.scalars())

        if not edges:
            ctx["surprises_cross_community"] = []
            return ctx

        # Build entity → community map
        ec_result = await db.execute(
            select(
                entity_community_subq.c.entity_id,
                entity_community_subq.c.community_name,
            )
        )
        entity_community: dict[str, str] = {}
        for row in ec_result:
            entity_community[row[0]] = row[1]

        # Fetch entity names
        entity_ids = set()
        for e in edges:
            entity_ids.add(e.entity_a_id)
            entity_ids.add(e.entity_b_id)

        name_result = await db.execute(
            select(EntityCanonical.id, EntityCanonical.canonical_name).where(
                EntityCanonical.id.in_(entity_ids)
            )
        )
        entity_names: dict[str, str] = {row[0]: row[1] for row in name_result}

        surprises = []
        for edge in edges:
            if len(surprises) >= limit:
                break
            com_a = entity_community.get(edge.entity_a_id)
            com_b = entity_community.get(edge.entity_b_id)
            if com_a and com_b and com_a != com_b:
                name_a = entity_names.get(edge.entity_a_id, edge.entity_a_id)
                name_b = entity_names.get(edge.entity_b_id, edge.entity_b_id)
                surprises.append(
                    {
                        "entity_a": name_a,
                        "entity_b": name_b,
                        "entity_a_id": edge.entity_a_id,
                        "entity_b_id": edge.entity_b_id,
                        "strategy": "cross_community",
                        "signal_breakdown": {
                            "cooccurrence": edge.cooccurrence_count,
                            "session_overlap": edge.session_overlap,
                            "adamic_adar": edge.adamic_adar,
                            "type_affinity": edge.type_affinity,
                            "semantic_similarity": edge.semantic_similarity,
                        },
                        "explanation": (
                            f"{name_a} ({com_a}) ↔ {name_b} ({com_b}): "
                            f"strong cross-community bridge (w={edge.composite_weight:.3f})"
                        ),
                        "community_a": com_a,
                        "community_b": com_b,
                    }
                )

        ctx["surprises_cross_community"] = surprises
        logger.info("Surprise cross_community: %d found", len(surprises))
        return ctx


class SurpriseKnowledgeGapOp(MemvaultOp):
    """High type affinity but low composite weight = expected connections that lack evidence."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ("db", "space_id")

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("surprises_knowledge_gap",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        from ..kg_models import EntityCanonical, EntityEdge

        db: AsyncSession = ctx["db"]
        space_id: str = ctx["space_id"]
        limit: int = self._config.surprise_limit

        # P25 of composite_weight as upper threshold
        p25_subq = (
            select(
                func.percentile_cont(0.25).within_group(EntityEdge.composite_weight)
            )
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.composite_weight > 0,
            )
            .scalar_subquery()
        )

        ea = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("ea")
        eb = select(EntityCanonical.id, EntityCanonical.canonical_name).subquery("eb")

        stmt = (
            select(
                EntityEdge,
                ea.c.canonical_name.label("name_a"),
                eb.c.canonical_name.label("name_b"),
            )
            .join(ea, EntityEdge.entity_a_id == ea.c.id)
            .join(eb, EntityEdge.entity_b_id == eb.c.id)
            .where(
                EntityEdge.space_id == space_id,
                EntityEdge.deleted_at.is_(None),
                EntityEdge.type_affinity >= 0.7,
                EntityEdge.composite_weight <= p25_subq,
            )
            .order_by(EntityEdge.type_affinity.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)

        surprises = []
        for row in result:
            edge = row[0]
            surprises.append(
                {
                    "entity_a": row.name_a,
                    "entity_b": row.name_b,
                    "entity_a_id": edge.entity_a_id,
                    "entity_b_id": edge.entity_b_id,
                    "strategy": "knowledge_gap",
                    "signal_breakdown": {
                        "cooccurrence": edge.cooccurrence_count,
                        "session_overlap": edge.session_overlap,
                        "adamic_adar": edge.adamic_adar,
                        "type_affinity": edge.type_affinity,
                        "semantic_similarity": edge.semantic_similarity,
                    },
                    "explanation": (
                        f"{row.name_a} and {row.name_b} are highly type-compatible "
                        f"(affinity={edge.type_affinity:.2f}) but have weak evidence "
                        f"(weight={edge.composite_weight:.3f})"
                    ),
                }
            )

        ctx["surprises_knowledge_gap"] = surprises
        logger.info("Surprise knowledge_gap: %d found", len(surprises))
        return ctx


class MergeSurprisesOp(MemvaultOp):
    """Collect all surprises_* keys into a single list (same pattern as MergeFindingsOp)."""

    @property
    def input_keys(self) -> tuple[str, ...]:
        return ()  # dynamically scans ctx

    @property
    def output_keys(self) -> tuple[str, ...]:
        return ("surprises",)

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        merged = []
        for key, value in ctx.items():
            if key.startswith("surprises_") and isinstance(value, list):
                merged.extend(value)
        ctx["surprises"] = merged
        logger.info("Merged %d total surprise connections", len(merged))
        return ctx
