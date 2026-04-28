"""Interest Profile Service — aggregates query history into user interest snapshots.

Generates daily snapshots from query_journal, computes attention profiles,
and detects knowledge gaps. No LLM required — pure SQL aggregation.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class InterestProfileService:
    """Aggregates query_journal data into interest snapshots and attention profiles."""

    async def generate_daily_snapshot(
        self,
        db: AsyncSession,
        space_id: str,
        target_date: date | None = None,
    ) -> dict:
        """Generate a daily interest snapshot from query_journal.

        Returns the snapshot data dict (also persisted to interest_snapshots table).
        """
        from .models import InterestSnapshot, QueryJournal

        if target_date is None:
            target_date = date.today()

        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)

        # --- Query volume and avg quality ---
        stats_q = select(
            func.count().label("volume"),
            func.avg(QueryJournal.evaluation_score).label("avg_quality"),
        ).where(
            QueryJournal.space_id == space_id,
            QueryJournal.created_at >= day_start,
            QueryJournal.created_at < day_end,
            QueryJournal.deleted_at.is_(None),
        )
        stats = (await db.execute(stats_q)).one()
        query_volume = stats.volume or 0

        if query_volume == 0:
            logger.info("No queries for %s on %s — skipping snapshot", space_id, target_date)
            return {"skipped": True, "reason": "no_queries"}

        avg_quality = round(float(stats.avg_quality or 0), 3)

        # --- Top intents (GROUP BY routing_intent) ---
        intent_q = (
            select(
                QueryJournal.routing_intent,
                func.count().label("cnt"),
            )
            .where(
                QueryJournal.space_id == space_id,
                QueryJournal.created_at >= day_start,
                QueryJournal.created_at < day_end,
                QueryJournal.routing_intent.isnot(None),
                QueryJournal.deleted_at.is_(None),
            )
            .group_by(QueryJournal.routing_intent)
            .order_by(func.count().desc())
        )
        intent_rows = (await db.execute(intent_q)).all()
        top_intents = {row.routing_intent: row.cnt for row in intent_rows}

        # --- Top entities (unnest top_entity_ids, count) ---
        # Using raw SQL for unnest since SQLAlchemy doesn't natively support it well
        entity_sql = text("""
            SELECT entity_id, COUNT(*) as cnt
            FROM memvault.query_journal, unnest(top_entity_ids) AS entity_id
            WHERE space_id = :space_id
              AND created_at >= :day_start AND created_at < :day_end
              AND deleted_at IS NULL
            GROUP BY entity_id
            ORDER BY cnt DESC
            LIMIT 20
        """)
        entity_rows = (await db.execute(
            entity_sql, {"space_id": space_id, "day_start": day_start, "day_end": day_end}
        )).all()
        top_entities = [{"entity_id": row.entity_id, "count": row.cnt} for row in entity_rows]

        # --- Knowledge gaps (queries with verdict INCORRECT, grouped) ---
        gaps_q = (
            select(
                QueryJournal.query_text,
                QueryJournal.query_hash,
                func.count().label("fail_count"),
            )
            .where(
                QueryJournal.space_id == space_id,
                QueryJournal.created_at >= day_start,
                QueryJournal.created_at < day_end,
                QueryJournal.evaluation_verdict == "INCORRECT",
                QueryJournal.deleted_at.is_(None),
            )
            .group_by(QueryJournal.query_text, QueryJournal.query_hash)
            .order_by(func.count().desc())
            .limit(10)
        )
        gap_rows = (await db.execute(gaps_q)).all()
        knowledge_gaps = [
            {"query": row.query_text, "query_hash": row.query_hash, "fail_count": row.fail_count}
            for row in gap_rows
        ]

        # --- Attention profile (7d / 30d / 90d windows) ---
        attention = await self._compute_attention_profile(db, space_id, target_date)

        # --- Persist snapshot ---
        snapshot = InterestSnapshot(
            space_id=space_id,
            created_by=None,
            snapshot_date=day_start,
            period="daily",
            top_intents=top_intents,
            top_entities=top_entities,
            top_communities=[],  # TODO: join with community data
            knowledge_gaps=knowledge_gaps,
            attention_profile=attention,
            query_volume=query_volume,
            avg_result_quality=avg_quality,
        )
        db.add(snapshot)
        await db.flush()

        logger.info(
            "Interest snapshot generated: space=%s date=%s queries=%d entities=%d gaps=%d",
            space_id, target_date, query_volume, len(top_entities), len(knowledge_gaps),
        )

        return {
            "snapshot_id": snapshot.id,
            "snapshot_date": str(target_date),
            "query_volume": query_volume,
            "top_intents": top_intents,
            "top_entities_count": len(top_entities),
            "knowledge_gaps_count": len(knowledge_gaps),
            "attention_entities": len(attention),
        }

    async def _compute_attention_profile(
        self,
        db: AsyncSession,
        space_id: str,
        reference_date: date,
    ) -> dict:
        """Compute attention level for each entity based on query recency.

        Three levels:
          active: queried in last 7 days
          historical: queried in last 7-30 days
          fading: queried in last 30-90 days
        """
        ref_dt = datetime(reference_date.year, reference_date.month, reference_date.day, tzinfo=UTC)
        d7 = ref_dt - timedelta(days=7)
        d30 = ref_dt - timedelta(days=30)
        d90 = ref_dt - timedelta(days=90)

        # Get all entities mentioned in last 90 days with their most recent query date
        entity_sql = text("""
            SELECT entity_id, MAX(qj.created_at) as last_queried
            FROM memvault.query_journal qj, unnest(qj.top_entity_ids) AS entity_id
            WHERE qj.space_id = :space_id
              AND qj.created_at >= :d90
              AND qj.deleted_at IS NULL
            GROUP BY entity_id
        """)
        rows = (await db.execute(
            entity_sql, {"space_id": space_id, "d90": d90}
        )).all()

        profile: dict[str, str] = {}
        for row in rows:
            last_q = row.last_queried
            if last_q.tzinfo is None:
                last_q = last_q.replace(tzinfo=UTC)
            if last_q >= d7:
                profile[row.entity_id] = "active"
            elif last_q >= d30:
                profile[row.entity_id] = "historical"
            else:
                profile[row.entity_id] = "fading"

        return profile

    async def get_attention_profile(self, db: AsyncSession, space_id: str) -> dict:
        """Get the latest attention profile from the most recent snapshot.

        This is what the PersonalizedQueryRouter reads (cached via Redis).
        """
        from .models import InterestSnapshot

        q = (
            select(InterestSnapshot.attention_profile)
            .where(
                InterestSnapshot.space_id == space_id,
                InterestSnapshot.deleted_at.is_(None),
                InterestSnapshot.attention_profile.isnot(None),
            )
            .order_by(InterestSnapshot.snapshot_date.desc())
            .limit(1)
        )
        row = (await db.execute(q)).scalar_one_or_none()
        return row or {}

    async def get_knowledge_gaps(
        self,
        db: AsyncSession,
        space_id: str,
        days: int = 7,
        limit: int = 10,
    ) -> list[dict]:
        """Get recurring knowledge gaps from the last N days.

        Finds queries that repeatedly got INCORRECT verdicts — the strongest
        signal that knowledge is missing.
        """
        from .models import QueryJournal

        cutoff = datetime.now(UTC) - timedelta(days=days)

        gaps_q = (
            select(
                QueryJournal.query_text,
                QueryJournal.query_hash,
                func.count().label("fail_count"),
                func.max(QueryJournal.created_at).label("last_failed"),
            )
            .where(
                QueryJournal.space_id == space_id,
                QueryJournal.evaluation_verdict == "INCORRECT",
                QueryJournal.created_at >= cutoff,
                QueryJournal.deleted_at.is_(None),
            )
            .group_by(QueryJournal.query_text, QueryJournal.query_hash)
            .having(func.count() >= 2)  # At least 2 failures
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = (await db.execute(gaps_q)).all()
        return [
            {
                "query": row.query_text,
                "query_hash": row.query_hash,
                "fail_count": row.fail_count,
                "last_failed": row.last_failed.isoformat() if row.last_failed else None,
            }
            for row in rows
        ]


interest_profile_service = InterestProfileService()
