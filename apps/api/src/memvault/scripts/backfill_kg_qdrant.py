"""Backfill KG data (triples, attitude blocks) into Qdrant.

Run from workshop root:
    cd core && ../.venv/bin/python3 src/modules/memvault/scripts/backfill_kg_qdrant.py
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_here = Path(__file__).resolve()
_core_root = _here.parents[4]  # .../core
if str(_core_root) not in sys.path:
    sys.path.insert(0, str(_core_root))

for _env_path in [_core_root / ".env", _core_root.parent / ".env"]:
    if _env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(_env_path)
        break

# ---------------------------------------------------------------------------
# Imports (after path bootstrap)
# ---------------------------------------------------------------------------
from sqlalchemy import create_engine, text  # noqa: E402

from src.shared.qdrant_search import index_documents_batch, init_collection  # noqa: E402
from src.shared.search_types import IndexDocument  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_raw_url = os.environ.get(
    "CORE_DB_URL",
    "postgresql://joneshong:REDACTED@localhost/workshop",
)
# Ensure psycopg3 driver (project uses psycopg, not psycopg2)
DATABASE_URL = (
    _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "+" not in _raw_url.split("://")[0]
    else _raw_url
)
BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_triple_doc(row) -> IndexDocument:
    return IndexDocument(
        service_id="memvault-triple",
        entity_id=str(row.id),
        entity_type="triple",
        space_id=str(row.space_id),
        content=f"{row.subject} {row.predicate} {row.object}",
        tags=[row.subject, row.predicate],
        created_at=row.created_at if isinstance(row.created_at, datetime) else None,
    )


def _row_to_attitude_doc(row) -> IndexDocument:
    tags = row.tags if isinstance(row.tags, list) else []
    return IndexDocument(
        service_id="memvault-attitude",
        entity_id=str(row.id),
        entity_type="attitude",
        space_id=str(row.space_id),
        content=row.content,
        tags=tags,
        created_at=row.created_at if isinstance(row.created_at, datetime) else None,
    )


def _row_to_community_doc(row) -> IndexDocument:
    parts = [row.name]
    if row.summary:
        parts.append(row.summary)
    if row.top_entities:
        # top_entities is a JSONB array
        entities = row.top_entities if isinstance(row.top_entities, list) else []
        if entities:
            parts.append(f"Entities: {', '.join(entities[:10])}")
    if row.top_predicates:
        predicates = row.top_predicates if isinstance(row.top_predicates, list) else []
        if predicates:
            parts.append(f"Predicates: {', '.join(predicates[:5])}")
    return IndexDocument(
        service_id="memvault-community",
        entity_id=str(row.id),
        entity_type="community",
        space_id=str(row.space_id),
        content="\n".join(parts),
        tags=(row.top_entities or [])[:5] if isinstance(row.top_entities, list) else [],
        created_at=row.created_at if isinstance(row.created_at, datetime) else None,
    )


def _row_to_summary_doc(row) -> IndexDocument:
    parts = [row.summary]
    if row.key_findings:
        findings = row.key_findings if isinstance(row.key_findings, list) else []
        parts.extend(findings)
    return IndexDocument(
        service_id="memvault-summary",
        entity_id=str(row.id),
        entity_type="community_summary",
        space_id=str(row.space_id),
        content="\n".join(parts),
        tags=row.tags if isinstance(row.tags, list) else [],
        created_at=row.created_at if isinstance(row.created_at, datetime) else None,
    )


async def _index_in_batches(
    docs: list[IndexDocument],
    label: str,
    batch_size: int = BATCH_SIZE,
) -> int:
    total = len(docs)
    indexed = 0
    for start in range(0, total, batch_size):
        batch = docs[start : start + batch_size]
        count = await index_documents_batch(batch)
        indexed += count
        end = min(start + batch_size, total)
        print(f"  {label}: {end}/{total} processed, {indexed} indexed so far")
    return indexed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=== KG → Qdrant Backfill ===")
    print(f"Database: {DATABASE_URL.split('@')[-1]}")

    # 1. Init Qdrant collection
    print("\n[1/5] Initialising Qdrant collection...")
    ok = await init_collection()
    if not ok:
        print("ERROR: Qdrant unavailable. Aborting.")
        sys.exit(1)
    print("      Collection ready.")

    # 2. Fetch KG records (sync engine — simpler, no asyncpg needed)
    print("\n[2/5] Fetching KG records...")
    engine = create_engine(DATABASE_URL, echo=False)
    with engine.connect() as conn:
        triple_rows = conn.execute(
            text(
                "SELECT id, space_id, subject, predicate, object, created_at"
                " FROM memvault.triples WHERE invalid_at IS NULL ORDER BY created_at"
            )
        ).fetchall()
        attitude_rows = conn.execute(
            text(
                "SELECT id, space_id, content, tags, created_at"
                " FROM memvault.blocks"
                " WHERE block_type = 'attitude'"
                " AND deleted_at IS NULL"
                " AND invalid_at IS NULL"
                " ORDER BY created_at"
            )
        ).fetchall()
        community_rows = conn.execute(
            text(
                "SELECT id, space_id, name, summary, top_entities, top_predicates, created_at"
                " FROM memvault.communities ORDER BY created_at"
            )
        ).fetchall()
        summary_rows = conn.execute(
            text(
                "SELECT id, space_id, summary, key_findings, tags, created_at"
                " FROM memvault.community_summaries ORDER BY created_at"
            )
        ).fetchall()
    engine.dispose()

    print(f"      Found {len(triple_rows)} valid triples")
    print(f"      Found {len(attitude_rows)} attitude blocks")
    print(f"      Found {len(community_rows)} communities (L1)")
    print(f"      Found {len(summary_rows)} community summaries (L2)")

    if not any([triple_rows, attitude_rows, community_rows, summary_rows]):
        print("\nNothing to index. Done.")
        return

    triple_docs = [_row_to_triple_doc(r) for r in triple_rows]
    attitude_docs = [_row_to_attitude_doc(r) for r in attitude_rows]
    community_docs = [_row_to_community_doc(r) for r in community_rows]
    summary_docs = [_row_to_summary_doc(r) for r in summary_rows]

    # 3. Index triples + attitude blocks
    print(f"\n[3/5] Indexing triples + attitude blocks (batch_size={BATCH_SIZE})...")
    t_idx = a_idx = 0
    if triple_docs:
        print(f"\n  --- Triples ({len(triple_docs)}) ---")
        t_idx = await _index_in_batches(triple_docs, "triples")
    if attitude_docs:
        print(f"\n  --- Attitude blocks ({len(attitude_docs)}) ---")
        a_idx = await _index_in_batches(attitude_docs, "attitude-blocks")

    # 4. Index communities (L1)
    print(f"\n[4/5] Indexing communities L1 ({len(community_docs)})...")
    c_idx = 0
    if community_docs:
        c_idx = await _index_in_batches(community_docs, "communities")

    # 5. Index community summaries (L2)
    print(f"\n[5/5] Indexing community summaries L2 ({len(summary_docs)})...")
    s_idx = 0
    if summary_docs:
        s_idx = await _index_in_batches(summary_docs, "summaries")

    # Summary
    total_ok = t_idx + a_idx + c_idx + s_idx
    total_all = len(triple_docs) + len(attitude_docs) + len(community_docs) + len(summary_docs)
    print(f"\n=== Summary: {total_ok}/{total_all} indexed ===")
    print(f"  Triples: {t_idx}/{len(triple_docs)}")
    print(f"  Attitude blocks: {a_idx}/{len(attitude_docs)}")
    print(f"  Communities (L1): {c_idx}/{len(community_docs)}")
    print(f"  Summaries (L2): {s_idx}/{len(summary_docs)}")
    if total_ok < total_all:
        print(f"  WARNING: {total_all - total_ok} failed")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
