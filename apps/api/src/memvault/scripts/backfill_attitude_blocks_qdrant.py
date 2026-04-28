"""Phase A2: 將 KAS 遷移產生的 attitude blocks 索引至 Qdrant memvault collection.

Run from workshop root:
    cd core && uv run python src/modules/memvault/scripts/backfill_attitude_blocks_qdrant.py

這個腳本是冪等的 (idempotent) — 重複執行不會產生重複 entry（Qdrant upsert）。
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
    "postgresql://joneshong:@localhost/workshop",
)
DATABASE_URL = (
    _raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if "+" not in _raw_url.split("://")[0]
    else _raw_url
)
BATCH_SIZE = 50


def _row_to_block_doc(row) -> IndexDocument:
    tags = row.tags if isinstance(row.tags, list) else []
    return IndexDocument(
        service_id="memvault",
        entity_id=str(row.id),
        entity_type="block",
        space_id=str(row.space_id),
        content=row.content,
        tags=tags,
        created_at=row.created_at if isinstance(row.created_at, datetime) else None,
        metadata={"block_type": row.block_type},
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


async def main() -> None:
    print("=== KAS Phase A2: attitude blocks → Qdrant backfill ===")
    print(f"Database: {DATABASE_URL.split('@')[-1]}")

    # 1. Init Qdrant collection
    print("\n[1/3] Initialising Qdrant collection...")
    ok = await init_collection()
    if not ok:
        print("ERROR: Qdrant unavailable. Aborting.")
        sys.exit(1)
    print("      Collection ready.")

    # 2. Fetch all attitude blocks
    print("\n[2/3] Fetching attitude blocks from DB...")
    engine = create_engine(DATABASE_URL, echo=False)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, space_id, content, block_type, tags, created_at"
                " FROM memvault.blocks"
                " WHERE block_type = 'attitude'"
                " AND deleted_at IS NULL"
                " AND invalid_at IS NULL"
                " ORDER BY created_at"
            )
        ).fetchall()
    engine.dispose()

    print(f"      Found {len(rows)} attitude blocks")
    if not rows:
        print("\nNothing to index. Done.")
        return

    docs = [_row_to_block_doc(r) for r in rows]

    # 3. Index into memvault Qdrant collection
    print(f"\n[3/3] Indexing {len(docs)} attitude blocks (batch_size={BATCH_SIZE})...")
    indexed = await _index_in_batches(docs, "attitude-blocks")

    print(f"\n=== Summary: {indexed}/{len(docs)} indexed ===")
    if indexed < len(docs):
        print(f"  WARNING: {len(docs) - indexed} failed to index")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
