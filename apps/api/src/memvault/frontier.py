"""Frontier signal aggregator — "what should I think about next?".

Aggregates three pre-existing signals into a single per-entity score:

    score(node) = ppr_centrality
                * log(out_degree + 1)
                * exp(-days_since_updated / 30)
                * knowledge_gap_bonus

Sources (all read-only, no schema changes):
  - PPR centrality:  EntityEdge.composite_weight summed per entity  (proxy)
                     dream.py runs igraph pagerank ad-hoc and does not persist
                     it, so we use the multi-signal composite_weight aggregate
                     as a stable approximation.
  - out_degree:      COUNT(*) on Triple grouped by canonical_subject_id
  - recency:         EntityCanonical.updated_at
  - knowledge_gap:   InterestSnapshot.knowledge_gaps (most recent snapshot
                     in the space; 1.5x bonus if entity name is listed).

This module deliberately does NOT touch dream.py / lint.py / sleeptime.py —
it only consumes their durable artifacts. See plan: Worker 1.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables (kept module-local; promote to settings if reused elsewhere)
# ---------------------------------------------------------------------------

RECENCY_TAU_DAYS = 30.0  # exp(-days / tau) — half-life ~ tau * ln(2)
KNOWLEDGE_GAP_BONUS = 1.5
DEFAULT_BONUS = 1.0
MAX_CANDIDATE_ENTITIES = 500  # safety cap on candidate set size


# ---------------------------------------------------------------------------
# Pure scoring (no DB) — exercised by unit tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierScore:
    """Per-entity frontier score with component breakdown."""

    entity_id: str
    entity_name: str
    score: float
    ppr: float
    out_degree: int
    days_since_updated: float
    knowledge_gap_bonus: float


def compute_frontier_score(
    *,
    entity_id: str,
    entity_name: str,
    ppr: float,
    out_degree: int,
    days_since_updated: float,
    is_in_knowledge_gaps: bool,
    tau_days: float = RECENCY_TAU_DAYS,
) -> FrontierScore:
    """Pure scoring function — deterministic, no I/O.

    Boundaries:
      - ppr <= 0 (orphan)        -> final score = 0
      - very stale node          -> recency factor -> ~0
      - knowledge_gaps empty     -> bonus = 1.0 (neutral)
    """
    # Defensive clamps — protect log/exp from undefined inputs.
    ppr = max(0.0, float(ppr))
    out_degree = max(0, int(out_degree))
    days_since_updated = max(0.0, float(days_since_updated))
    tau = max(1e-6, float(tau_days))

    if ppr == 0.0:
        # Orphan / never-linked entity — short-circuit to 0 to avoid
        # noise from synthetic recency or gap bonuses.
        return FrontierScore(
            entity_id=entity_id,
            entity_name=entity_name,
            score=0.0,
            ppr=0.0,
            out_degree=out_degree,
            days_since_updated=days_since_updated,
            knowledge_gap_bonus=DEFAULT_BONUS,
        )

    log_term = math.log(out_degree + 1)
    recency = math.exp(-days_since_updated / tau)
    gap_bonus = KNOWLEDGE_GAP_BONUS if is_in_knowledge_gaps else DEFAULT_BONUS

    score = ppr * log_term * recency * gap_bonus
    return FrontierScore(
        entity_id=entity_id,
        entity_name=entity_name,
        score=score,
        ppr=ppr,
        out_degree=out_degree,
        days_since_updated=days_since_updated,
        knowledge_gap_bonus=gap_bonus,
    )


def rank_top_n(scores: Iterable[FrontierScore], n: int) -> list[FrontierScore]:
    """Sort by score desc, drop zeros, take top n."""
    nonzero = [s for s in scores if s.score > 0.0]
    nonzero.sort(key=lambda s: s.score, reverse=True)
    return nonzero[: max(0, int(n))]


# ---------------------------------------------------------------------------
# Service — pulls signals from DB and applies compute_frontier_score
# ---------------------------------------------------------------------------


class FrontierService:
    """Reads PPR proxy / out_degree / recency / knowledge_gaps and ranks."""

    async def compute_top(
        self,
        db: AsyncSession,
        space_id: str,
        n: int = 5,
        *,
        now: datetime | None = None,
    ) -> list[FrontierScore]:
        """Return top-N frontier candidates for a space.

        Empty graph → empty list (no error).
        """
        # Local imports keep stub-based unit tests from importing SA stack.
        from .kg_models import EntityCanonical, EntityEdge, Triple
        from .models import InterestSnapshot

        now = now or datetime.now(UTC)

        # ---- 1. PPR proxy: sum composite_weight per entity from EntityEdge.
        # Edges are stored once (entity_a_id < entity_b_id) so we sum both
        # endpoints to get the entity-level centrality proxy.
        ppr_a_q = (
            select(
                EntityEdge.entity_a_id.label("eid"),
                func.coalesce(func.sum(EntityEdge.composite_weight), 0.0).label("w"),
            )
            .where(EntityEdge.space_id == space_id)
            .group_by(EntityEdge.entity_a_id)
        )
        ppr_b_q = (
            select(
                EntityEdge.entity_b_id.label("eid"),
                func.coalesce(func.sum(EntityEdge.composite_weight), 0.0).label("w"),
            )
            .where(EntityEdge.space_id == space_id)
            .group_by(EntityEdge.entity_b_id)
        )
        ppr_map: dict[str, float] = {}
        for row in (await db.execute(ppr_a_q)).all():
            ppr_map[row.eid] = ppr_map.get(row.eid, 0.0) + float(row.w or 0.0)
        for row in (await db.execute(ppr_b_q)).all():
            ppr_map[row.eid] = ppr_map.get(row.eid, 0.0) + float(row.w or 0.0)

        # ---- 2. out_degree from Triple grouped by canonical_subject_id (valid only).
        deg_q = (
            select(
                Triple.canonical_subject_id.label("eid"),
                func.count().label("d"),
            )
            .where(
                Triple.space_id == space_id,
                Triple.invalid_at.is_(None),
                Triple.canonical_subject_id.is_not(None),
            )
            .group_by(Triple.canonical_subject_id)
        )
        deg_map: dict[str, int] = {
            row.eid: int(row.d) for row in (await db.execute(deg_q)).all() if row.eid
        }

        # ---- 3. Candidate set = union(ppr_map, deg_map). If both empty, no graph.
        candidate_ids: set[str] = set(ppr_map.keys()) | set(deg_map.keys())
        if not candidate_ids:
            return []
        # Cap candidates to prevent unbounded ent fetch on very large graphs.
        if len(candidate_ids) > MAX_CANDIDATE_ENTITIES:
            # Prefer high-PPR ones first.
            ranked = sorted(
                candidate_ids,
                key=lambda eid: ppr_map.get(eid, 0.0),
                reverse=True,
            )
            candidate_ids = set(ranked[:MAX_CANDIDATE_ENTITIES])

        # ---- 4. Fetch entity name + updated_at for candidates.
        ent_q = select(
            EntityCanonical.id,
            EntityCanonical.canonical_name,
            EntityCanonical.updated_at,
        ).where(
            EntityCanonical.space_id == space_id,
            EntityCanonical.id.in_(candidate_ids),
        )
        ent_rows = (await db.execute(ent_q)).all()

        # ---- 5. Latest knowledge_gaps for the space (most recent snapshot).
        gap_q = (
            select(InterestSnapshot.knowledge_gaps)
            .where(InterestSnapshot.space_id == space_id)
            .order_by(desc(InterestSnapshot.snapshot_date))
            .limit(1)
        )
        gap_row = (await db.execute(gap_q)).first()
        gaps_set = _normalize_gaps(gap_row[0] if gap_row else None)

        # ---- 6. Score each candidate.
        scored: list[FrontierScore] = []
        for ent_id, ent_name, updated_at in ent_rows:
            ppr = ppr_map.get(ent_id, 0.0)
            deg = deg_map.get(ent_id, 0)
            days = _days_between(updated_at, now)
            in_gap = bool(ent_name) and ent_name.lower() in gaps_set
            scored.append(
                compute_frontier_score(
                    entity_id=ent_id,
                    entity_name=ent_name or "",
                    ppr=ppr,
                    out_degree=deg,
                    days_since_updated=days,
                    is_in_knowledge_gaps=in_gap,
                )
            )

        return rank_top_n(scored, n)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_between(then: datetime | None, now: datetime) -> float:
    if then is None:
        # Unknown freshness → treat as moderately stale (one tau).
        return RECENCY_TAU_DAYS
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    delta = now - then
    return max(0.0, delta.total_seconds() / 86400.0)


def _normalize_gaps(raw: object) -> set[str]:
    """knowledge_gaps is JSONB — accept list[str] or list[dict{name|entity}]."""
    if not raw:
        return set()
    out: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.add(item.strip().lower())
            elif isinstance(item, Mapping):
                name = item.get("name") or item.get("entity") or item.get("canonical_name")
                if isinstance(name, str):
                    out.add(name.strip().lower())
    elif isinstance(raw, Mapping):
        # Tolerate dict-of-name->meta shape.
        for k in raw.keys():
            if isinstance(k, str):
                out.add(k.strip().lower())
    return out


# Module-level singleton (matches services.py convention)
frontier_service = FrontierService()
