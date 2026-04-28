"""Entity Resolution — normalize, match, merge entities across KG triples.

Three-tier strategy:
  1. Deterministic: case-fold + whitespace + Unicode NFC (free, always)
  2. Embedding similarity > 0.92 (cheap, batch)
  3. LLM-powered merge (expensive, optional, future)
"""

import logging
import re
import unicodedata

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.events_stub.bus import Event, event_bus
from src.events_stub.types import MemvaultEvents
from src.shared.text_utils import is_cjk

from .kg_models import EntityCanonical, Triple
from .kg_schemas import EntityCanonicalResponse, EntityMergeResult, EntityResolutionStats

logger = logging.getLogger(__name__)


def normalize_entity_text(text: str) -> str:
    """Deterministic normalization — zero cost, run on every ingest.

    1. Unicode NFC (handles full/half-width CJK)
    2. Strip + collapse whitespace
    3. Case-fold for non-CJK
    4. Strip trailing punctuation
    """
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text.strip())
    if not is_cjk(text):
        text = text.lower()
    text = text.rstrip(".,;:!?\u3002\u3001\uff1b\uff1a\uff01\uff1f")
    return text


class EntityResolutionService:
    """Resolve raw entity strings to canonical forms."""

    def to_response(self, instance: EntityCanonical) -> EntityCanonicalResponse:
        return EntityCanonicalResponse(
            id=instance.id,
            space_id=instance.space_id,
            created_by=instance.created_by,
            created_at=instance.created_at,
            updated_at=instance.updated_at,
            canonical_name=instance.canonical_name,
            aliases=instance.aliases or [],
            entity_type=instance.entity_type or "concept",
            merge_count=instance.merge_count or 1,
        )

    async def resolve_entity(
        self,
        db: AsyncSession,
        space_id: str,
        raw_text: str,
        entity_type: str = "concept",
    ) -> EntityCanonical:
        """Resolve a raw entity string to its canonical form.

        1. Normalize text
        2. Exact match on canonical_name
        3. Check aliases (ANY array match)
        4. If no match → create new
        """
        normalized = normalize_entity_text(raw_text)

        # Exact match on canonical_name + entity_type
        q = select(EntityCanonical).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.canonical_name == normalized,
            EntityCanonical.entity_type == entity_type,
        )
        existing = (await db.execute(q)).scalar_one_or_none()
        if existing:
            # Add raw_text as alias if different from canonical
            if raw_text != normalized and raw_text not in (existing.aliases or []):
                aliases = list(existing.aliases or [])
                aliases.append(raw_text)
                existing.aliases = aliases
                await db.flush()
            return existing

        # Check aliases (with entity_type)
        alias_q = select(EntityCanonical).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.entity_type == entity_type,
            EntityCanonical.aliases.any(normalized),
        )
        alias_match = (await db.execute(alias_q)).scalar_one_or_none()
        if alias_match:
            return alias_match

        # Create new canonical entity
        aliases = [normalized]
        if raw_text != normalized:
            aliases.append(raw_text)

        # embedding column removed (Qdrant migration)
        entity = EntityCanonical(
            space_id=space_id,
            canonical_name=normalized,
            aliases=aliases,
            entity_type=entity_type,
        )
        db.add(entity)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # Race condition: someone else created it
            existing = (
                await db.execute(
                    select(EntityCanonical).where(
                        EntityCanonical.space_id == space_id,
                        EntityCanonical.canonical_name == normalized,
                    )
                )
            ).scalar_one()
            return existing

        return entity

    async def resolve_and_link_triple(
        self,
        db: AsyncSession,
        space_id: str,
        triple: Triple,
    ) -> Triple:
        """Resolve subject and object of a triple, set FK fields."""
        subj_entity = await self.resolve_entity(db, space_id, triple.subject)
        triple.canonical_subject_id = subj_entity.id

        obj_entity = await self.resolve_entity(db, space_id, triple.object)
        triple.canonical_object_id = obj_entity.id

        await db.flush()
        return triple

    async def batch_resolve_triples(
        self,
        db: AsyncSession,
        space_id: str,
        triples: list[Triple],
    ) -> int:
        """Batch resolve entities for a list of triples."""
        resolved = 0
        for triple in triples:
            if triple.canonical_subject_id and triple.canonical_object_id:
                continue
            await self.resolve_and_link_triple(db, space_id, triple)
            resolved += 1
        return resolved

    async def merge_entities(
        self,
        db: AsyncSession,
        primary_id: str,
        secondary_id: str,
    ) -> EntityMergeResult:
        """Merge secondary entity into primary."""
        primary = await db.get(EntityCanonical, primary_id)
        secondary = await db.get(EntityCanonical, secondary_id)
        if not primary or not secondary:
            from src.shared.errors import NotFoundError

            raise NotFoundError("Entity not found", code="memvault.entity_not_found")

        # Union aliases
        merged_aliases = list(set((primary.aliases or []) + (secondary.aliases or [])))
        primary.aliases = merged_aliases
        primary.merge_count = (primary.merge_count or 1) + (secondary.merge_count or 1)

        # Update all triples referencing secondary → primary
        subj_result = await db.execute(
            update(Triple)
            .where(Triple.canonical_subject_id == secondary_id)
            .values(canonical_subject_id=primary_id)
        )
        obj_result = await db.execute(
            update(Triple)
            .where(Triple.canonical_object_id == secondary_id)
            .values(canonical_object_id=primary_id)
        )
        triples_updated = (subj_result.rowcount or 0) + (obj_result.rowcount or 0)

        # Soft-delete secondary
        from datetime import UTC, datetime

        secondary.deleted_at = datetime.now(UTC)
        await db.flush()

        event_bus.publish_fire_and_forget(
            Event(
                type=MemvaultEvents.ENTITY_MERGED,
                data={
                    "primary_id": primary_id,
                    "secondary_id": secondary_id,
                    "triples_updated": triples_updated,
                },
                source="memvault",
            )
        )

        return EntityMergeResult(
            merged_id=primary_id,
            canonical_name=primary.canonical_name,
            aliases=merged_aliases,
            triples_updated=triples_updated,
        )

    async def expand_query(
        self,
        db: AsyncSession,
        space_id: str,
        entity_text: str,
    ) -> list[str]:
        """Return canonical_name + all aliases for query expansion."""
        normalized = normalize_entity_text(entity_text)
        q = select(EntityCanonical).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.canonical_name == normalized,
        )
        entity = (await db.execute(q)).scalar_one_or_none()
        if entity:
            return list(set([entity.canonical_name] + (entity.aliases or [])))
        return [entity_text]

    async def get_stats(
        self,
        db: AsyncSession,
        space_id: str,
    ) -> EntityResolutionStats:
        """Aggregate entity resolution statistics."""
        total_q = (
            select(func.count())
            .select_from(EntityCanonical)
            .where(
                EntityCanonical.space_id == space_id,
                EntityCanonical.deleted_at.is_(None),
            )
        )
        total = (await db.execute(total_q)).scalar_one()

        alias_q = select(
            func.coalesce(func.sum(func.array_length(EntityCanonical.aliases, 1)), 0)
        ).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.deleted_at.is_(None),
        )
        total_aliases = (await db.execute(alias_q)).scalar_one()

        avg_q = select(func.coalesce(func.avg(EntityCanonical.merge_count), 1.0)).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.deleted_at.is_(None),
        )
        avg_merge = (await db.execute(avg_q)).scalar_one()

        unresolved_q = (
            select(func.count())
            .select_from(Triple)
            .where(
                Triple.space_id == space_id,
                Triple.deleted_at.is_(None),
                Triple.canonical_subject_id.is_(None),
            )
        )
        unresolved = (await db.execute(unresolved_q)).scalar_one()

        return EntityResolutionStats(
            total_entities=total or 0,
            total_aliases=total_aliases or 0,
            avg_merge_count=round(float(avg_merge or 1.0), 2),
            unresolved_triples=unresolved or 0,
        )

    async def find_merge_candidates(
        self,
        db: AsyncSession,
        space_id: str,
        threshold: float = 0.92,
        limit: int = 50,
    ) -> list[tuple[EntityCanonicalResponse, EntityCanonicalResponse, float]]:
        """Scan for potential merges via embedding similarity.

        NOTE: pgvector path removed — EntityCanonical.embedding column dropped in Qdrant migration.
        TODO(qdrant): Implement via Qdrant once EntityCanonical records are indexed
        (service_id="memvault", entity_type="entity_canonical"). Use hybrid_search per entity
        with top_k=3, score_threshold=threshold to replace the former O(n²) loop.
        """
        return []

    async def auto_merge(
        self,
        db: AsyncSession,
        space_id: str,
        threshold: float = 0.95,
        max_merges: int = 20,
    ) -> list[EntityMergeResult]:
        """Auto-merge entity pairs above threshold. Safe for post-ingest use.

        Only merges pairs with similarity >= threshold (default 0.95).
        Stops after max_merges to bound execution time.
        """
        candidates = await self.find_merge_candidates(
            db, space_id, threshold=threshold, limit=max_merges * 2
        )
        results = []
        merged_ids: set[str] = set()

        for primary_resp, secondary_resp, similarity in candidates:
            if len(results) >= max_merges:
                break
            # Skip if either entity was already merged in this batch
            if primary_resp.id in merged_ids or secondary_resp.id in merged_ids:
                continue
            try:
                result = await self.merge_entities(db, primary_resp.id, secondary_resp.id)
                merged_ids.add(secondary_resp.id)
                results.append(result)
                logger.info(
                    "Auto-merged: %s <- %s (sim=%.3f)",
                    primary_resp.canonical_name,
                    secondary_resp.canonical_name,
                    similarity,
                )
            except Exception:
                logger.warning(
                    "Auto-merge failed: %s <- %s",
                    primary_resp.canonical_name,
                    secondary_resp.canonical_name,
                    exc_info=True,
                )

        return results


entity_resolution_service = EntityResolutionService()
