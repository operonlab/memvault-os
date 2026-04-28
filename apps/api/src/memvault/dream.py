"""Memvault Dream Loop — automated memory consolidation pipeline.

Inspired by Claude Code's Auto-Dream, this pipeline composes existing modules
(curate, lint, dedup, conflict_resolver) into a 4-phase consolidation loop:

  Phase 1 — Orient:         stats snapshot + dual-gate trigger check
  Phase 2 — Gather Signal:  scan recent blocks + broad contradiction detection
  Phase 2.5 — Reflect:     LLM reflective pass — insights, gaps, merge candidates
  Phase 3 — Consolidate:   contradiction resolution + batch dedup + content normalization
  Phase 4 — Prune & Report: curate + lint remediation + event publish

Dual-gate trigger: (now - last_dream_at) > 24h AND sessions_since >= 5.
Each phase is isolated — a failure in one phase does not abort subsequent phases.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic_ai import Agent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from .llm_config import make_litellm_model
from .llm_models import DreamReflectionOutput

logger = logging.getLogger(__name__)

# --- Configuration ---
DUAL_GATE_HOURS = 24
DUAL_GATE_SESSIONS = 5
CONTRADICTION_SAMPLE_SIZE = 300
MAX_CONTRADICTIONS_PER_RUN = 30
BATCH_DEDUP_THRESHOLD = 0.92
BATCH_DEDUP_PAGE_SIZE = 50
MAX_MERGES_PER_RUN = 50
REDIS_KEY_LAST_DREAM = "memvault:dream:last_run_at"

# --- LLM Reflection ---
_REFLECT_MAX_BLOCKS = 15
_REFLECT_MAX_ATTITUDES = 15

_reflect_agent = Agent(
    output_type=DreamReflectionOutput,
    system_prompt=(
        "You are performing a dream — a reflective pass over a personal knowledge management "
        "system. Your goal is to synthesize recent learning into durable, organized insights.\n\n"
        "Guidelines:\n"
        "- insights: max 3 items, one sentence each\n"
        "- merge_candidates: max 3 items, brief description\n"
        "- knowledge_gaps: max 3 items, topic name only\n"
        "- suggested_attitudes: max 3 items, one sentence each\n"
        "- stale_candidates: max 2 items, brief description\n"
        "- Be extremely concise. Each item under 50 chars. Write in 繁體中文."
    ),
    retries=2,
)

# --- Data Structures ---


@dataclass
class DreamReport:
    phase_orient: dict = field(default_factory=dict)
    phase_signal: dict = field(default_factory=dict)
    phase_reflect: dict = field(default_factory=dict)
    phase_consolidate: dict = field(default_factory=dict)
    phase_prune: dict = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    dry_run: bool = True
    skipped: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d


# --- Phase Implementations ---


async def _orient(
    db: AsyncSession,
    space_id: str,
    force: bool,
) -> tuple[dict, bool]:
    """Phase 1: Gather stats and check dual-gate trigger.

    Returns (stats_dict, should_proceed).
    """
    from sqlalchemy import func, select

    from .models import MemoryBlock

    # Block stats by type
    q = (
        select(MemoryBlock.block_type, func.count())
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
        )
        .group_by(MemoryBlock.block_type)
    )
    rows = (await db.execute(q)).all()
    block_stats = {row[0]: row[1] for row in rows}
    total_blocks = sum(block_stats.values())

    # Last dream timestamp from Redis
    last_dream_at = None
    try:
        from src.shared.redis import get_redis

        redis = get_redis()
        val = await redis.get(REDIS_KEY_LAST_DREAM)
        if val:
            last_dream_at = datetime.fromisoformat(val.decode())
    except Exception:
        logger.debug("dream.orient: Redis unavailable, treating as first run")

    # Sessions since last dream
    sessions_since = 0
    if last_dream_at:
        sq = select(func.count(func.distinct(MemoryBlock.source_session))).where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.source_session.isnot(None),
            MemoryBlock.created_at > last_dream_at,
        )
        sessions_since = (await db.execute(sq)).scalar() or 0
    else:
        sessions_since = DUAL_GATE_SESSIONS  # first run: pass gate

    now = datetime.now(UTC)
    hours_since = (
        (now - last_dream_at).total_seconds() / 3600 if last_dream_at else DUAL_GATE_HOURS + 1
    )

    # Count stale community summaries (updated > 30 days ago)
    stale_summaries = 0
    try:
        from .kg_models import CommunitySummary

        stale_cutoff = now - timedelta(days=30)
        stale_q = select(func.count()).where(
            CommunitySummary.space_id == space_id,
            CommunitySummary.updated_at < stale_cutoff,
        )
        stale_summaries = (await db.execute(stale_q)).scalar() or 0
    except Exception:
        logger.debug("dream.orient: stale summary count failed")

    stats = {
        "total_blocks": total_blocks,
        "block_stats": block_stats,
        "last_dream_at": last_dream_at.isoformat() if last_dream_at else None,
        "sessions_since": sessions_since,
        "hours_since": round(hours_since, 1),
        "stale_summaries": stale_summaries,
    }

    # Dual-gate check
    if force:
        stats["trigger"] = "forced"
        return stats, True

    gate_time = hours_since >= DUAL_GATE_HOURS
    gate_sessions = sessions_since >= DUAL_GATE_SESSIONS
    if gate_time and gate_sessions:
        stats["trigger"] = "dual_gate_passed"
        return stats, True

    stats["trigger"] = "skipped"
    stats["gate_time"] = gate_time
    stats["gate_sessions"] = gate_sessions
    return stats, False


async def _gather_signal(
    db: AsyncSession,
    space_id: str,
    last_dream_at: str | None,
) -> dict:
    """Phase 2: Scan recent blocks and detect contradictions broadly."""
    from sqlalchemy import func, select

    from .lint import check_contradictions
    from .models import MemoryBlock

    since = (
        datetime.fromisoformat(last_dream_at)
        if last_dream_at
        else datetime.now(UTC) - timedelta(days=7)
    )

    # Count new blocks since last dream
    q = (
        select(MemoryBlock.block_type, func.count())
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.created_at > since,
        )
        .group_by(MemoryBlock.block_type)
    )
    rows = (await db.execute(q)).all()
    new_blocks = {row[0]: row[1] for row in rows}

    # Broad contradiction scan
    contradiction_findings = await check_contradictions(
        db, space_id, sample_size=CONTRADICTION_SAMPLE_SIZE
    )
    resolvable = [f for f in contradiction_findings if f.suggested_action == "resolve"]

    # PPR centrality analysis — identify hub and orphan entities
    hub_entities: list[str] = []
    orphan_entities: list[str] = []
    try:
        from .kg_models import Triple

        triple_q = (
            select(Triple.subject, Triple.object)
            .where(
                Triple.space_id == space_id,
                Triple.invalid_at.is_(None),
            )
            .limit(2000)
        )
        triple_rows = (await db.execute(triple_q)).all()
        if len(triple_rows) >= 50:
            from kg_ops.community import build_entity_graph
            from kg_ops.pagerank import global_pagerank

            triple_dicts = [{"subject": r[0], "object": r[1]} for r in triple_rows]
            graph, _idx = build_entity_graph(triple_dicts)
            pr_results = global_pagerank(graph, top_k=graph.vcount())
            if pr_results:
                hub_entities = [name for name, _ in pr_results[:10]]
                orphan_entities = [name for name, score in pr_results[-10:] if score < 0.001]
    except Exception:
        logger.debug("dream.gather_signal: PPR centrality failed", exc_info=True)

    return {
        "new_blocks_since_last": new_blocks,
        "total_new": sum(new_blocks.values()),
        "contradictions_found": len(contradiction_findings),
        "contradictions_resolvable": len(resolvable),
        "hub_entities": hub_entities,
        "orphan_entities": orphan_entities,
        "_findings": resolvable,  # internal, passed to consolidate
    }


async def _reflect(
    db: AsyncSession,
    space_id: str,
    orient: dict,
    signal: dict,
) -> dict:
    """Phase 2.5: LLM reflective pass over memory state.

    Feeds recent blocks + attitudes + contradiction summary to Haiku,
    which returns structured insights about the memory state.
    """
    from sqlalchemy import select

    from .models import MemoryBlock

    # Gather recent blocks
    bq = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(_REFLECT_MAX_BLOCKS)
    )
    blocks = (await db.execute(bq)).scalars().all()
    blocks_summary = "\n".join(f"- [{b.block_type}] {(b.content or '')[:80]}" for b in blocks)

    # Gather recent attitude blocks (KAS Phase B)
    aq = (
        select(MemoryBlock)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.block_type == "attitude",
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.invalid_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(_REFLECT_MAX_ATTITUDES)
    )
    attitudes = (await db.execute(aq)).scalars().all()
    attitudes_summary = "\n".join(
        f"- [{(a.tags or ['preference'])[0]}] {a.content} (confidence: {a.confidence:.2f})"
        for a in attitudes
    )

    contradictions = signal.get("contradictions_found", 0)
    new_blocks = signal.get("total_new", 0)

    user_message = (
        f"## Memory Stats\n"
        f"- Total blocks: {orient.get('total_blocks', 0)}\n"
        f"- New since last dream: {new_blocks}\n"
        f"- Contradictions detected: {contradictions}\n\n"
        f"## Recent Memory Blocks ({len(blocks)})\n{blocks_summary or '(none)'}\n\n"
        f"## Current Attitude Facts ({len(attitudes)})\n{attitudes_summary or '(none)'}"
    )

    try:
        result = await _reflect_agent.run(
            user_message,
            model=make_litellm_model("gemini-2.5-flash"),
            model_settings={"temperature": 0.3, "max_tokens": 16000, "timeout": 30},
        )
        return result.output.model_dump()

    except Exception as e:
        logger.warning("dream.reflect failed: %s", e)
        return {"error": str(e)}


async def _consolidate(
    db: AsyncSession,
    space_id: str,
    signal: dict,
    dry_run: bool,
) -> dict:
    """Phase 3: Resolve contradictions, batch dedup, normalize dates."""
    from sqlalchemy import select, update

    from src.shared.embedding import get_embedding
    from text_ops.merge import merge_content

    from .conflict_resolver import ConflictDecision, resolve_conflict
    from .dedup import check_duplicate
    from .fold_verifier import (
        compute_content_hash,
        compute_fold_id,
        pre_write_conflict_check,
        verify_fold_extractiveness,
    )
    from .kg_models import Triple
    from .models import MemoryBlock

    result = {
        "contradictions_resolved": 0,
        "contradictions_coexist": 0,
        "blocks_merged": 0,
        "blocks_skipped": 0,
        "content_normalized": 0,
        "norm_changes": {},  # per-op change counts
        # Worker 2: Verifier-Backed Extractive Fold + Dual-Key Idempotency
        "folds_skipped_idempotent": 0,   # fold_id + content_hash both match → no-op
        "folds_overwritten": 0,          # fold_id same, content drift → updated
        "folds_quarantined": 0,          # pre-write KG conflict → status=conflict_pending
        "fold_sentences_rejected": 0,    # verifier dropped non-extractive sentences
    }

    # Worker 2: Pre-write KG contradiction check (Mem0-style, fail-open).
    # We still run a once-per-run global check here purely for observability /
    # fail-open logging — the per-fold ``fold_status`` decision happens inside
    # the merge loop below, scoped to that fold's children. Reviewer caught the
    # earlier bug where this run-level value was reused for every fold (one
    # space-level contradiction quarantined the whole batch).
    pre_check = await pre_write_conflict_check(db, space_id)
    if pre_check.has_conflict:
        logger.info(
            "dream.consolidate.pre_check space=%s has space-level contradictions "
            "(%d finding(s)) — per-fold check still gates each fold",
            space_id,
            len(pre_check.findings),
        )
    if pre_check.error:
        logger.warning(
            "dream.consolidate.pre_check failed (fail-open): %s", pre_check.error
        )

    # --- 3a. Retroactive contradiction resolution ---
    findings = signal.get("_findings", [])
    for finding in findings[:MAX_CONTRADICTIONS_PER_RUN]:
        try:
            # Get the two triples involved (lint.py uses triple_a/triple_b keys)
            meta = finding.metadata
            triple_id = meta.get("triple_a") or finding.entity_id
            conflicting_id = meta.get("triple_b")
            if not conflicting_id:
                continue

            tq = select(Triple).where(Triple.id.in_([triple_id, conflicting_id]))
            triples = {t.id: t for t in (await db.execute(tq)).scalars().all()}
            if len(triples) < 2:
                continue

            t1 = triples.get(triple_id)
            t2 = triples.get(conflicting_id)
            if not t1 or not t2:
                continue

            content_a = f"{t1.subject} {t1.predicate} {t1.object}"
            content_b = f"{t2.subject} {t2.predicate} {t2.object}"

            cr = await resolve_conflict(
                new_content=content_a,
                existing_content=content_b,
                existing_block_id=conflicting_id,
                similarity=meta.get("similarity", 0.8),
            )

            if dry_run:
                if cr.decision == ConflictDecision.COEXIST:
                    result["contradictions_coexist"] += 1
                else:
                    result["contradictions_resolved"] += 1
                continue

            if cr.decision == ConflictDecision.SUPERSEDE:
                # Invalidate the older triple
                older = (
                    t2 if (t1.created_at or datetime.min) > (t2.created_at or datetime.min) else t1
                )
                await db.execute(
                    update(Triple)
                    .where(Triple.id == older.id)
                    .values(
                        invalid_at=datetime.now(UTC),
                        invalidation_reason=f"dream_supersede: {cr.reason[:100]}",
                    )
                )
                result["contradictions_resolved"] += 1
            elif cr.decision == ConflictDecision.MERGE:
                # Keep newer, invalidate older
                older = (
                    t2 if (t1.created_at or datetime.min) > (t2.created_at or datetime.min) else t1
                )
                await db.execute(
                    update(Triple)
                    .where(Triple.id == older.id)
                    .values(
                        invalid_at=datetime.now(UTC),
                        invalidation_reason=f"dream_merge: {cr.reason[:100]}",
                    )
                )
                result["contradictions_resolved"] += 1
            else:
                result["contradictions_coexist"] += 1

        except Exception as e:
            logger.warning("dream.consolidate.contradiction failed: %s", e)

    # --- 3b. Batch dedup scan ---
    merge_count = 0
    offset = 0
    try:
        while merge_count < MAX_MERGES_PER_RUN:
            bq = (
                select(MemoryBlock)
                .where(
                    MemoryBlock.space_id == space_id,
                    MemoryBlock.deleted_at.is_(None),
                    MemoryBlock.block_type != "skill",  # skip APPEND_ONLY
                )
                .order_by(MemoryBlock.created_at.asc())
                .offset(offset)
                .limit(BATCH_DEDUP_PAGE_SIZE)
            )
            blocks = (await db.execute(bq)).scalars().all()
            if not blocks:
                break

            for block in blocks:
                if merge_count >= MAX_MERGES_PER_RUN:
                    break

                embedding = await get_embedding(block.content or "")
                if embedding is None:
                    continue

                dedup_result = await check_duplicate(
                    db,
                    space_id,
                    block.content or "",
                    embedding,
                    threshold=BATCH_DEDUP_THRESHOLD,
                    block_type=block.block_type,
                )

                if dedup_result.decision.value == "merge" and dedup_result.existing_block_id:
                    if dedup_result.existing_block_id == str(block.id):
                        continue  # skip self-match

                    if dry_run:
                        merge_count += 1
                        continue

                    # Merge content into the existing block, soft-delete current
                    eq = select(MemoryBlock).where(MemoryBlock.id == dedup_result.existing_block_id)
                    existing = (await db.execute(eq)).scalar_one_or_none()
                    if existing:
                        # ----- Worker 2: dual-key idempotency + post-hoc verifier -----
                        # Fold = (existing) ← merge ← (block). Children = both ids.
                        children_ids = [str(existing.id), str(block.id)]
                        children_texts = [existing.content or "", block.content or ""]

                        merged_text = merge_content(
                            existing.content or "", block.content or ""
                        )

                        # Post-hoc verifier: drop sentences with no grounding in children.
                        try:
                            v = await verify_fold_extractiveness(
                                merged_text, children_texts
                            )
                            if v.rejected:
                                result["fold_sentences_rejected"] += len(v.rejected)
                                logger.info(
                                    "dream.consolidate.fold_verifier rejected %d sentence(s) "
                                    "for fold over %s",
                                    len(v.rejected),
                                    children_ids,
                                )
                            # If verifier zeroed out the text, fall back to the
                            # raw merged text — better to keep something than to
                            # silently destroy content.
                            verified_text = v.filtered_text or merged_text
                        except Exception as exc:
                            logger.warning(
                                "dream.consolidate.fold_verifier failed: %s — using raw merge",
                                exc,
                            )
                            verified_text = merged_text

                        new_fold_id = compute_fold_id(children_ids)
                        new_content_hash = compute_content_hash(verified_text)

                        # Dual-key decision tree:
                        # - same fold_id + same content_hash → idempotent skip
                        # - same fold_id + diff content_hash → overwrite (child drift)
                        # - new fold_id                       → fresh fold (default path)
                        if (
                            existing.fold_id == new_fold_id
                            and existing.content_hash == new_content_hash
                        ):
                            result["folds_skipped_idempotent"] += 1
                            # Children already absorbed in a prior dream — soft-delete the
                            # duplicate child anyway so it does not re-trigger every loop.
                            block.deleted_at = datetime.now(UTC)
                            await db.flush()
                            continue

                        # Per-fold conflict check (children differ across folds,
                        # so the scope MUST be per-fold — not the run-level
                        # ``pre_check`` above). On conflict, write the fold
                        # with status='conflict_pending' for human review.
                        try:
                            fold_pre_check = await pre_write_conflict_check(
                                db, space_id, children_ids=children_ids
                            )
                            fold_status = (
                                "conflict_pending"
                                if fold_pre_check.has_conflict
                                else "active"
                            )
                        except Exception as exc:
                            logger.warning(
                                "dream.consolidate.fold_pre_check failed "
                                "(fail-open): %s",
                                exc,
                            )
                            fold_status = "active"

                        overwriting = existing.fold_id == new_fold_id
                        existing.content = verified_text
                        existing.fold_id = new_fold_id
                        existing.content_hash = new_content_hash
                        existing.status = fold_status
                        block.deleted_at = datetime.now(UTC)
                        merge_count += 1

                        if overwriting:
                            result["folds_overwritten"] += 1
                        if fold_status == "conflict_pending":
                            result["folds_quarantined"] += 1

                        await db.flush()

                elif dedup_result.decision.value == "skip":
                    result["blocks_skipped"] += 1

            offset += BATCH_DEDUP_PAGE_SIZE

    except Exception as e:
        logger.warning("dream.consolidate.batch_dedup failed: %s", e)

    result["blocks_merged"] = merge_count

    # --- 3c. Content normalization (dates, currency, proportions, etc.) ---
    try:
        from .content_normalizer import ContentNormalizerPipeline, NormContext

        pipeline = ContentNormalizerPipeline(llm_refinement=False)
        norm_change_counts: dict[str, int] = {}

        dq = (
            select(MemoryBlock)
            .where(
                MemoryBlock.space_id == space_id,
                MemoryBlock.deleted_at.is_(None),
                MemoryBlock.content.isnot(None),
            )
            .limit(500)
        )
        blocks_to_check = (await db.execute(dq)).scalars().all()

        for block in blocks_to_check:
            if not block.content:
                continue
            ctx = NormContext(
                created_at=block.created_at or datetime.now(UTC),
                block_type=block.block_type or "knowledge",
                space_id=space_id,
            )
            norm_result = await pipeline.normalize(block.content, ctx)
            if norm_result.changed:
                if not dry_run:
                    block.content = norm_result.normalized
                result["content_normalized"] += 1
                for change in norm_result.changes:
                    norm_change_counts[change.op] = norm_change_counts.get(change.op, 0) + 1

        if not dry_run and result["content_normalized"] > 0:
            await db.flush()

        result["norm_changes"] = norm_change_counts

    except Exception as e:
        logger.warning("dream.consolidate.content_normalize failed: %s", e)

    return result


async def _prune_and_report(
    db: AsyncSession,
    space_id: str,
    dry_run: bool,
) -> dict:
    """Phase 4: Curate low-confidence blocks + lint remediation."""
    from .curate import curate_space
    from .lint import remediate_orphans, remediate_stale, run_lint

    result = {
        "curate": {},
        "lint_summary": {},
        "stale_remediated": 0,
        "orphans_remediated": 0,
    }

    # Curate
    try:
        curate_result = await curate_space(db, space_id, dry_run=dry_run)
        result["curate"] = curate_result
    except Exception as e:
        logger.warning("dream.prune.curate failed: %s", e)
        result["curate"] = {"error": str(e)}

    # Lint + remediation
    try:
        lint_report = await run_lint(
            db, space_id, checks=["stale", "orphan_entities", "dangling_refs"]
        )
        result["lint_summary"] = lint_report.summary

        stale_findings = [f for f in lint_report.findings if f.check == "stale"]
        orphan_findings = [f for f in lint_report.findings if f.check == "orphan_entities"]

        result["stale_remediated"] = await remediate_stale(db, stale_findings, dry_run=dry_run)
        result["orphans_remediated"] = await remediate_orphans(db, orphan_findings, dry_run=dry_run)

    except Exception as e:
        logger.warning("dream.prune.lint failed: %s", e)
        result["lint_summary"] = {"error": str(e)}

    # Mark stale community summaries for regeneration
    stale_summary_count = 0
    try:
        from sqlalchemy import func, select

        from .kg_models import CommunitySummary, CommunityTriple, Triple

        stale_cutoff = datetime.now(UTC) - timedelta(days=30)

        # Find summaries where > 50% of member triples were updated after the summary
        stale_ids: list[str] = []
        summaries = (
            await db.execute(
                select(CommunitySummary).where(
                    CommunitySummary.space_id == space_id,
                    CommunitySummary.updated_at < stale_cutoff,
                )
            )
        ).scalars().all()

        for summary in summaries:
            # Count member triples updated after this summary
            member_count = (
                await db.execute(
                    select(func.count()).select_from(CommunityTriple).where(
                        CommunityTriple.community_id == summary.community_id,
                    )
                )
            ).scalar() or 0

            if member_count == 0:
                continue

            updated_count = (
                await db.execute(
                    select(func.count())
                    .select_from(CommunityTriple)
                    .join(Triple, CommunityTriple.triple_id == Triple.id)
                    .where(
                        CommunityTriple.community_id == summary.community_id,
                        Triple.updated_at > summary.updated_at,
                    )
                )
            ).scalar() or 0

            if updated_count / member_count > 0.5:
                stale_ids.append(str(summary.id))

        if stale_ids and not dry_run:
            logger.info("dream.prune: marking %d stale summaries for regeneration", len(stale_ids))
            stale_summary_count = len(stale_ids)

        result["stale_summaries_flagged"] = len(stale_ids)

    except Exception as e:
        logger.debug("dream.prune.stale_summaries failed: %s", e)
        result["stale_summaries_flagged"] = 0

    return result


# --- Main Orchestrator ---


async def _run_dream_pipeline(
    db: AsyncSession,
    space_id: str,
    dry_run: bool,
    force: bool,
) -> DreamReport:
    """Run dream via Reactive Pipeline."""
    from .pipeline_config import MemvaultPipelineConfig
    from .pipelines.dream_pipeline import build_dream_pipeline

    config = MemvaultPipelineConfig.from_env()
    pipeline = build_dream_pipeline(config)

    ctx = await pipeline.execute({
        "db": db,
        "space_id": space_id,
        "dry_run": dry_run,
        "force": force,
    })

    meta = ctx.get("_pipeline_meta")
    report = DreamReport(dry_run=dry_run)
    report.phase_orient = ctx.get("orient_stats", {})
    report.phase_signal = ctx.get("signal_stats", {})
    report.phase_reflect = ctx.get("reflect_result", {})
    report.phase_consolidate = ctx.get("consolidate_stats", {})
    report.phase_prune = ctx.get("prune_stats", {})
    report.skipped = not ctx.get("should_proceed", False)
    report.finished_at = datetime.now(UTC)

    if meta and meta.stage_errors:
        report.errors = [f"{k}: {v[:200]}" for k, v in meta.stage_errors.items()]

    # Post-execution: Redis update + event publish (same as sequential path)
    if not dry_run and not report.skipped:
        try:
            from src.shared.redis import get_redis

            redis = get_redis()
            await redis.set(
                REDIS_KEY_LAST_DREAM,
                report.finished_at.isoformat(),
                ex=86400 * 30,
            )
        except Exception:
            logger.debug("dream: failed to update Redis last_dream_at")

        try:
            from src.events_stub.bus import Event, event_bus
            from src.events_stub.types import MemvaultEvents

            event_bus.publish_fire_and_forget(
                Event(
                    type=MemvaultEvents.DREAM_COMPLETED,
                    data=report.to_dict(),
                    source="memvault.dream",
                )
            )
        except Exception:
            logger.debug("dream: failed to publish DREAM_COMPLETED event")

    elapsed = (report.finished_at - report.started_at).total_seconds()
    logger.info(
        "dream.done(pipeline) dry_run=%s elapsed=%.1fs errors=%d",
        dry_run,
        elapsed,
        len(report.errors),
    )
    return report


async def run_dream(
    db: AsyncSession,
    space_id: str = "default",
    dry_run: bool = True,
    force: bool = False,
    use_pipeline: bool = False,
) -> DreamReport:
    """Execute the Dream Loop: Orient → Gather Signal → Consolidate → Prune.

    Args:
        db: Async database session.
        space_id: Space to consolidate.
        dry_run: If True, report what would change without mutations.
        force: If True, skip dual-gate trigger check.
        use_pipeline: If True, run via Reactive Pipeline instead of sequential path.

    Returns:
        DreamReport with per-phase results.
    """
    if use_pipeline:
        return await _run_dream_pipeline(db, space_id, dry_run, force)

    report = DreamReport(dry_run=dry_run)
    logger.info("dream.start space=%s dry_run=%s force=%s", space_id, dry_run, force)

    # Phase 1: Orient
    try:
        orient_stats, should_proceed = await _orient(db, space_id, force)
        report.phase_orient = orient_stats
        if not should_proceed:
            report.skipped = True
            report.finished_at = datetime.now(UTC)
            logger.info("dream.skipped reason=%s", orient_stats.get("trigger"))
            return report
    except Exception as e:
        report.errors.append(f"orient: {e}")
        logger.warning("dream.orient failed: %s", e, exc_info=True)

    # Phase 2: Gather Signal
    signal: dict = {}
    try:
        signal = await _gather_signal(db, space_id, report.phase_orient.get("last_dream_at"))
        # Don't expose internal _findings in the report
        report.phase_signal = {k: v for k, v in signal.items() if not k.startswith("_")}
    except Exception as e:
        report.errors.append(f"gather_signal: {e}")
        logger.warning("dream.gather_signal failed: %s", e, exc_info=True)

    # Phase 2.5: LLM Reflection
    try:
        report.phase_reflect = await _reflect(db, space_id, report.phase_orient, signal)
    except Exception as e:
        report.errors.append(f"reflect: {e}")
        logger.warning("dream.reflect failed: %s", e, exc_info=True)

    # Phase 3: Consolidate
    try:
        report.phase_consolidate = await _consolidate(db, space_id, signal, dry_run)
    except Exception as e:
        report.errors.append(f"consolidate: {e}")
        logger.warning("dream.consolidate failed: %s", e, exc_info=True)

    # Phase 4: Prune & Report
    try:
        report.phase_prune = await _prune_and_report(db, space_id, dry_run)
    except Exception as e:
        report.errors.append(f"prune: {e}")
        logger.warning("dream.prune failed: %s", e, exc_info=True)

    # Finalize
    report.finished_at = datetime.now(UTC)

    # Update last_dream_at in Redis (only on real runs)
    if not dry_run:
        try:
            from src.shared.redis import get_redis

            redis = get_redis()
            await redis.set(
                REDIS_KEY_LAST_DREAM,
                report.finished_at.isoformat(),
                ex=86400 * 30,  # 30 day TTL
            )
        except Exception:
            logger.debug("dream: failed to update Redis last_dream_at")

        # Publish completion event
        try:
            from src.events_stub.bus import Event, event_bus
            from src.events_stub.types import MemvaultEvents

            event_bus.publish_fire_and_forget(
                Event(
                    type=MemvaultEvents.DREAM_COMPLETED,
                    data=report.to_dict(),
                    source="memvault.dream",
                )
            )
        except Exception:
            logger.debug("dream: failed to publish DREAM_COMPLETED event")

    elapsed = (report.finished_at - report.started_at).total_seconds()
    logger.info(
        "dream.done dry_run=%s elapsed=%.1fs errors=%d",
        dry_run,
        elapsed,
        len(report.errors),
    )
    return report
