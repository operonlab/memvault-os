"""Adversary tests for check #10 — semantic_tiling_dedup.

Spec: block-block embedding cosine > threshold (default 0.92) → finding.
Boundary mutations:
  (a) cosine ≈ 0.95 (above 0.92) → finding
  (b) cosine ≈ 0.91 (below 0.92) → NO finding (boundary mutation guard)
  (c) embedding module unavailable → finding with metadata.skipped=True,
      severity not 'error' (graceful degradation, not crash)
"""

from __future__ import annotations

import math
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("sqlalchemy")


def _make_db(rows: list[tuple[str, str, str]]):
    """rows: list of (block_id, content, block_type)."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _vec_from_cosine(cosine: float) -> list[float]:
    """Build a 4D unit vector that has the requested cosine with [1,0,0,0]."""
    s = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return [cosine, s, 0.0, 0.0]


@pytest.mark.asyncio
async def test_semantic_dedup_high_cosine_flagged():
    """(a) Two near-identical blocks (cosine 0.95) → finding."""
    from src.memvault.lint_checks.semantic_tiling_dedup import check_semantic_tiling_dedup

    rows = [
        ("a" * 32, "Postgres is a relational database engine.", "knowledge"),
        ("b" * 32, "Postgres is a relational DB engine.", "knowledge"),
    ]
    db = _make_db(rows)

    # Vector for block A is canonical [1,0,0,0]; vector for block B yields cos=0.95.
    fake_vec_a = [1.0, 0.0, 0.0, 0.0]
    fake_vec_b = _vec_from_cosine(0.95)

    async def fake_embed_batch(texts):
        # Return one vector per input text in order
        return [fake_vec_a, fake_vec_b][: len(texts)]

    fake_embedding_mod = types.ModuleType("src.memvault.embedding")
    fake_embedding_mod.get_embeddings_batch = fake_embed_batch
    with patch.dict(sys.modules, {"src.memvault.embedding": fake_embedding_mod}):
        findings = await check_semantic_tiling_dedup(db, space_id="space-1")

    dedup_findings = [
        f for f in findings if f.check == "semantic_tiling_dedup" and not f.metadata.get("skipped")
    ]
    assert len(dedup_findings) >= 1, (
        "BUG INDICATOR: cosine 0.95 (above default 0.92 threshold) did not "
        "produce a dedup finding."
    )


@pytest.mark.asyncio
async def test_semantic_dedup_below_threshold_silent():
    """(b) Cosine 0.91 (below default 0.92) → NO finding.

    Mutation guard: if author wrote `>=` against threshold or used a lower
    threshold like 0.9, this triggers a false-positive."""
    from src.memvault.lint_checks.semantic_tiling_dedup import check_semantic_tiling_dedup

    rows = [
        ("a" * 32, "Distinct content one about gardens.", "knowledge"),
        ("b" * 32, "Different content two about kernels.", "knowledge"),
    ]
    db = _make_db(rows)

    fake_vec_a = [1.0, 0.0, 0.0, 0.0]
    fake_vec_b = _vec_from_cosine(0.91)

    async def fake_embed_batch(texts):
        return [fake_vec_a, fake_vec_b][: len(texts)]

    fake_embedding_mod = types.ModuleType("src.memvault.embedding")
    fake_embedding_mod.get_embeddings_batch = fake_embed_batch
    with patch.dict(sys.modules, {"src.memvault.embedding": fake_embedding_mod}):
        findings = await check_semantic_tiling_dedup(db, space_id="space-1")

    dedup_findings = [
        f for f in findings if f.check == "semantic_tiling_dedup" and not f.metadata.get("skipped")
    ]
    assert dedup_findings == [], (
        "BUG INDICATOR: cosine 0.91 (below default 0.92 threshold) yielded a "
        "dedup finding. Threshold predicate likely uses `>=` or wrong constant."
    )


@pytest.mark.asyncio
async def test_semantic_dedup_embedding_unavailable_graceful():
    """(c) Embedding module raises on import → return single finding with
    metadata.skipped=True, no crash."""
    from src.memvault.lint_checks.semantic_tiling_dedup import check_semantic_tiling_dedup

    rows = [
        ("a" * 32, "any content here.", "knowledge"),
        ("b" * 32, "another piece of content.", "knowledge"),
    ]
    db = _make_db(rows)

    # Make `from ..embedding import get_embeddings_batch` raise.
    broken_mod = types.ModuleType("src.memvault.embedding")

    def _raise(*a, **kw):
        raise RuntimeError("MLX worker not wired")

    # Accessing the attribute via `from x import y` triggers __getattr__.
    broken_mod.__getattr__ = lambda name: _raise()  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"src.memvault.embedding": broken_mod}):
        # Must NOT raise
        findings = await check_semantic_tiling_dedup(db, space_id="space-1")

    skipped = [f for f in findings if f.metadata.get("skipped")]
    assert len(skipped) == 1, (
        "BUG INDICATOR: embedding-module-unavailable did not produce the "
        "expected single skipped finding (graceful degradation contract)."
    )
    # Skipped reports should not be 'error' severity (would falsely escalate).
    assert skipped[0].severity != "error", (
        "BUG INDICATOR: skipped-due-to-missing-embedder marked as 'error' — "
        "would falsely surface in the critical bucket of the report."
    )
    assert skipped[0].check == "semantic_tiling_dedup"


@pytest.mark.asyncio
async def test_semantic_dedup_single_block_returns_empty():
    """Edge: only 1 block in space → no pairs to compare → empty list."""
    from src.memvault.lint_checks.semantic_tiling_dedup import check_semantic_tiling_dedup

    rows = [("a" * 32, "lone content.", "knowledge")]
    db = _make_db(rows)

    fake_embedding_mod = types.ModuleType("src.memvault.embedding")

    async def fake_embed_batch(texts):
        return [[1.0, 0.0, 0.0, 0.0]] * len(texts)

    fake_embedding_mod.get_embeddings_batch = fake_embed_batch
    with patch.dict(sys.modules, {"src.memvault.embedding": fake_embedding_mod}):
        findings = await check_semantic_tiling_dedup(db, space_id="space-1")

    dedup_findings = [
        f for f in findings if f.check == "semantic_tiling_dedup" and not f.metadata.get("skipped")
    ]
    assert dedup_findings == []
