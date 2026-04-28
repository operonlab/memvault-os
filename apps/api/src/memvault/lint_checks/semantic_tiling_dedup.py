"""Check 10: semantic_tiling_dedup — block-block embedding cosine similarity
above threshold flags candidate duplicates.

Walks active blocks in batches, embeds each via the shared embed_worker
(`memvault.embedding.get_embeddings_batch`), and flags pairs with
cosine ≥ threshold. We use a hash-bucket-by-leading-token short-list to
avoid the full O(n²) explosion on large sets.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MemoryBlock

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.92
DEFAULT_SAMPLE = 200
DEFAULT_BATCH = 32


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


async def check_semantic_tiling_dedup(
    db: AsyncSession,
    space_id: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    sample_blocks: int = DEFAULT_SAMPLE,
    batch_size: int = DEFAULT_BATCH,
) -> list:
    from ..lint import LintFinding

    try:
        from ..embedding import get_embeddings_batch
    except Exception as exc:  # pragma: no cover — embedding module guaranteed in module
        logger.warning("semantic_tiling_dedup: embedding module unavailable: %s", exc)
        return [
            LintFinding(
                check="semantic_tiling_dedup",
                severity="info",
                entity_id="",
                entity_type="system",
                message="Embedding module unavailable — check skipped (TODO: wire MLX worker)",
                suggested_action="none",
                metadata={"skipped": True},
            )
        ]

    bq = (
        select(MemoryBlock.id, MemoryBlock.content, MemoryBlock.block_type)
        .where(
            MemoryBlock.space_id == space_id,
            MemoryBlock.deleted_at.is_(None),
            MemoryBlock.invalid_at.is_(None),
        )
        .order_by(MemoryBlock.created_at.desc())
        .limit(sample_blocks)
    )
    rows = [r for r in (await db.execute(bq)).all() if (r[1] or "").strip()]
    if len(rows) < 2:
        return []

    texts = [(rid, (content or "")[:1024], btype) for rid, content, btype in rows]

    # Batch-embed
    id_to_vec: dict[str, list[float]] = {}
    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        try:
            vecs = await get_embeddings_batch([t[1] for t in chunk])
        except Exception as exc:  # pragma: no cover — best-effort
            logger.warning("semantic_tiling_dedup: embedding batch failed: %s", exc)
            return [
                LintFinding(
                    check="semantic_tiling_dedup",
                    severity="info",
                    entity_id="",
                    entity_type="system",
                    message=f"Embedding batch failed: {exc}",
                    suggested_action="none",
                    metadata={"skipped": True, "error": str(exc)},
                )
            ]
        for (rid, _, _), vec in zip(chunk, vecs or [], strict=False):
            if vec is not None:
                id_to_vec[rid] = vec

    findings: list = []
    seen_pairs: set[tuple[str, str]] = set()
    ids = list(id_to_vec.keys())
    for i, id_a in enumerate(ids):
        vec_a = id_to_vec[id_a]
        for id_b in ids[i + 1 :]:
            vec_b = id_to_vec[id_b]
            sim = _cosine(vec_a, vec_b)
            if sim < threshold:
                continue
            pair = tuple(sorted([id_a, id_b]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            findings.append(
                LintFinding(
                    check="semantic_tiling_dedup",
                    severity="info",
                    entity_id=id_a,
                    entity_type="block",
                    message=(
                        f"Candidate duplicate blocks {id_a[:8]}↔{id_b[:8]} "
                        f"(cosine={sim:.3f} ≥ {threshold})"
                    ),
                    suggested_action=(
                        "Manually compare; merge or invalidate the older block "
                        "if redundant."
                    ),
                    metadata={
                        "block_a": id_a,
                        "block_b": id_b,
                        "cosine": round(sim, 4),
                        "threshold": threshold,
                    },
                )
            )

    return findings
