# Embedding ORM/Runtime Drift Patches — Phase 0.2

**Background**: `MemoryBlock.embedding` and `Triple.embedding` columns were removed during the pgvector → Qdrant migration, but four code sites still write/query `.embedding` on the SA model, causing runtime `AttributeError` / SQL errors. Strategy A: route everything through Qdrant; do **not** re-add the ORM column.

**Verified sources**:
- `core/src/modules/memvault/models.py` — `MemoryBlock` has no `embedding` column (verified L18-53)
- `core/src/modules/memvault/kg_models.py` — `Triple` has no `embedding` column (verified L1-80)
- `core/src/shared/search_types.py:8-20` — `IndexDocument` is **content-driven** (no `vector=` param)
- `core/src/shared/qdrant_search.py` — `index_document`, `index_documents_batch`, `delete_document` exist; **no scroll helper**
- `core/src/shared/qdrant_client.py:20` — `get_client() -> AsyncQdrantClient | None`

**Drift sweep result** (`grep -rn "\.embedding\s*=" core/src/modules/memvault/`):

| # | File | Line | Pattern |
|---|------|------|---------|
| 1 | `kg_routes.py` | 74 | `instance.embedding = embedding` (SA write) |
| 2 | `kg_routes.py` | 500 | `Triple.embedding.is_(None)` (SA query) |
| 3 | `kg_routes.py` | 513 | `t.embedding = emb` (SA write in loop) |
| 4 | `services.py` | 995 | `update(MemoryBlock).values(embedding=...)` (SA update) |

No 5th drift site found in memvault module. (Note: `services.py:982-1000` defines `MemoryBlockService.update_embedding(block_id, embedding)` — the **method body** is one of the 4 sites; its **callers** still pass through legitimately and will silently become a no-op write target — see § 4 follow-up.)

---

## 1. `kg_routes.py:74` — Triple create endpoint

### Original (L62-78)

```python
@router.post("/triples", response_model=TripleResponse, status_code=201)
async def create_triple(
    body: TripleCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await triple_service.create(db, space_id, body)
    # Generate embedding (best-effort)
    embedding_text = f"{instance.subject} {instance.predicate} {instance.object}"
    embedding = await get_embedding(embedding_text)
    if embedding:
        instance.embedding = embedding         # ← drift: AttributeError
        await db.flush()
    await db.commit()
    await db.refresh(instance)
    return triple_service.to_response(instance)
```

### Patched

```python
@router.post("/triples", response_model=TripleResponse, status_code=201)
async def create_triple(
    body: TripleCreate,
    space_id: str = Query("default"),
    db: AsyncSession = Depends(get_db),
    _user: dict = require_permission("memvault.write"),
):
    instance = await triple_service.create(db, space_id, body)
    await db.commit()
    await db.refresh(instance)

    # Index to Qdrant (content-driven; embedding generated inside index_document)
    try:
        await qdrant_search.index_document(
            IndexDocument(
                service_id="memvault",
                entity_id=instance.id,
                entity_type="triple",
                space_id=space_id,
                content=f"{instance.subject} {instance.predicate} {instance.object}",
                created_at=instance.created_at,
                updated_at=instance.updated_at,
                metadata={
                    "subject": instance.subject,
                    "predicate": instance.predicate,
                    "object": instance.object,
                },
            )
        )
    except Exception as e:  # best-effort — never block the write
        logger.warning("Qdrant index failed for triple %s: %s", instance.id, e)

    return triple_service.to_response(instance)
```

### Dependency diff

```diff
- from .embedding import get_embedding, get_embeddings_batch
+ from .embedding import get_embeddings_batch  # still used by L510 batch path
+ from src.shared import qdrant_search
+ from src.shared.search_types import IndexDocument
+ import logging
+ logger = logging.getLogger(__name__)        # add at module top if absent
```

`get_embedding` import at L15 becomes dead in this file once §3 is also patched — keep `get_embeddings_batch` only.

### Test plan

1. `POST /api/memvault/kg/triples?space_id=test` with `{subject, predicate, object}` body
2. Response 201 with `TripleResponse` payload
3. Postgres: `SELECT * FROM memvault.triples WHERE id = <id>` row exists, **no embedding column**
4. Qdrant: `curl http://localhost:6333/collections/workshop-docs-1024/points/scroll -d '{"filter":{"must":[{"key":"service_id","match":{"value":"memvault"}},{"key":"entity_type","match":{"value":"triple"}},{"key":"entity_id","match":{"value":"<id>"}}]},"limit":1}'` returns 1 point with `subject/predicate/object` in payload metadata
5. Restart core, re-POST same triple — idempotent (point_id is UUIDv5(`memvault:<id>`), upserts in place)

---

## 2. `kg_routes.py:500` — Backfill triple selection (find unindexed)

### Original (L482-520)

```python
@router.post("/embeddings/backfill", status_code=200)
async def backfill_embeddings(
    space_id: str = Query("default"),
    batch_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select
    from .kg_models import Triple

    # --- Triples ---
    triple_q = (
        select(Triple)
        .where(Triple.space_id == space_id, Triple.embedding.is_(None))   # ← drift
        .order_by(Triple.created_at)
    )
    result = await db.execute(triple_q)
    triples = list(result.scalars().all())

    triple_updated = 0
    for i in range(0, len(triples), batch_size):
        batch = triples[i : i + batch_size]
        texts = [f"{t.subject} {t.predicate} {t.object}" for t in batch]
        embeddings = await get_embeddings_batch(texts)
        for t, emb in zip(batch, embeddings, strict=True):
            if emb:
                t.embedding = emb              # ← drift (covered in §3)
                triple_updated += 1
        await db.flush()

    await db.commit()
    return {
        "triples": {"total_missing": len(triples), "updated": triple_updated},
    }
```

### Patched

The "find unindexed" semantic flips from "Postgres NULL embedding" to "Postgres rows whose `id` is NOT present in Qdrant for `service_id=memvault, entity_type=triple, space_id=<space_id>`". Use the new `scroll_by_service` helper (§5) to materialise the set of already-indexed entity_ids, then compute the diff in Python (or `NOT IN` in SQL — Python is simpler and matches typical backfill scale).

```python
@router.post("/embeddings/backfill", status_code=200)
async def backfill_embeddings(
    space_id: str = Query("default"),
    batch_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Backfill missing Qdrant entries for triples.

    Diff-driven: scroll Qdrant for indexed entity_ids, LEFT-anti-join against
    Postgres triples to find unindexed rows, then batch-index via Qdrant.
    """
    from sqlalchemy import select
    from .kg_models import Triple

    # 1. Snapshot already-indexed entity_ids from Qdrant
    indexed_ids = await qdrant_search.scroll_by_service(
        service_id="memvault",
        entity_type="triple",
        space_id=space_id,
    )

    # 2. Fetch all Postgres triples for this space
    triple_q = (
        select(Triple)
        .where(Triple.space_id == space_id)
        .order_by(Triple.created_at)
    )
    result = await db.execute(triple_q)
    all_triples = list(result.scalars().all())

    # 3. Anti-join: keep only triples not yet in Qdrant
    missing = [t for t in all_triples if t.id not in indexed_ids]

    triple_updated = 0
    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        docs = [
            IndexDocument(
                service_id="memvault",
                entity_id=t.id,
                entity_type="triple",
                space_id=space_id,
                content=f"{t.subject} {t.predicate} {t.object}",
                created_at=t.created_at,
                updated_at=t.updated_at,
                metadata={
                    "subject": t.subject,
                    "predicate": t.predicate,
                    "object": t.object,
                },
            )
            for t in batch
        ]
        triple_updated += await qdrant_search.index_documents_batch(docs)

    return {
        "triples": {
            "total_missing": len(missing),
            "updated": triple_updated,
            "already_indexed": len(indexed_ids),
        },
    }
```

### Dependency diff

```diff
- # uses .embedding column + get_embeddings_batch + manual SA write
+ from src.shared import qdrant_search
+ from src.shared.search_types import IndexDocument
- from .embedding import get_embeddings_batch  # only if no other caller in file
```

`get_embeddings_batch` is no longer needed in `kg_routes.py` after §3 is also patched — drop the import.

### SQL alternative (if memory pressure becomes a concern)

For very large spaces, replace step 3 with a chunked NOT IN query:

```python
# Page through Postgres by created_at, filter via Python set
indexed_ids: set[str] = await qdrant_search.scroll_by_service(...)
missing: list[Triple] = []
async for t in stream_triples(db, space_id):  # hypothetical generator
    if t.id not in indexed_ids:
        missing.append(t)
```

For Phase 0.2, the in-memory diff is acceptable (memvault per-space typically < 100k triples).

### Test plan

1. Seed: `POST /triples` × 5 → all auto-indexed by §1 patch
2. `DELETE` 2 Qdrant points via `qdrant_search.delete_document("memvault", <id>)`
3. `POST /api/memvault/kg/embeddings/backfill?space_id=test`
4. Response: `{"triples": {"total_missing": 2, "updated": 2, "already_indexed": 3}}`
5. Repeat → `total_missing: 0, updated: 0, already_indexed: 5` (idempotent)

---

## 3. `kg_routes.py:513` — Backfill embedding write

### Status

Subsumed by §2's patched function body — the `for t, emb in zip(batch, embeddings, strict=True): t.embedding = emb` loop is replaced by `qdrant_search.index_documents_batch(docs)`. No standalone change needed here; **the patch in §2 covers L513**.

### Verification

After applying §2, `grep -n "\.embedding" kg_routes.py` should return zero hits.

---

## 4. `services.py:995` — `MemoryBlockService.update_embedding`

### Original (L982-1000)

```python
async def update_embedding(
    self, db: AsyncSession, block_id: str, embedding: list[float]
) -> None:
    """Set or update the embedding vector for a block.

    Writes to both inline column (backward compat) and sub-table (Phase 2).
    """
    if len(embedding) != EMBEDDING_DIM:
        raise BadRequestError(
            f"Embedding must be {EMBEDDING_DIM}d",
            code="memvault.invalid_embedding_dim",
        )
    result = await db.execute(
        update(MemoryBlock).where(MemoryBlock.id == block_id).values(embedding=embedding)  # ← drift
    )
    if result.rowcount == 0:
        raise NotFoundError("Block not found", code="memvault.block_not_found")

    # BlockEmbedding sub-table removed (Qdrant migration) — inline embedding only
```

### Patched

The signature must change: `IndexDocument` is content-driven, so a pre-computed `list[float]` is not directly accepted by `qdrant_search.index_document`. Two options:

- **A. Re-fetch block, index by content** (recommended — consistent with §1, content is the single source of truth)
- **B. Add `vector` override path to `index_document`** (intrusive — touches shared layer)

Take Option A:

```python
async def update_embedding(
    self, db: AsyncSession, block_id: str, embedding: list[float] | None = None
) -> None:
    """Re-index a block to Qdrant.

    Note: post-Qdrant migration, the `embedding` argument is ignored — Qdrant
    re-embeds from `block.content`. The argument is kept for caller compatibility
    and may be removed in Phase 1. Pre-computed embeddings should be discarded
    in favour of fresh content-driven embeds (single source of truth).
    """
    if embedding is not None and len(embedding) != EMBEDDING_DIM:
        raise BadRequestError(
            f"Embedding must be {EMBEDDING_DIM}d",
            code="memvault.invalid_embedding_dim",
        )

    # Fetch block to verify existence and pull content
    q = select(MemoryBlock).where(
        MemoryBlock.id == block_id,
        MemoryBlock.deleted_at.is_(None),
    )
    block = (await db.execute(q)).scalars().first()
    if block is None:
        raise NotFoundError("Block not found", code="memvault.block_not_found")

    ok = await qdrant_search.index_document(
        IndexDocument(
            service_id="memvault",
            entity_id=block.id,
            entity_type="block",
            space_id=block.space_id,
            content=block.content,
            tags=block.tags or [],
            created_at=block.created_at,
            updated_at=block.updated_at,
            metadata={
                "block_type": block.block_type,
                "confidence": block.confidence,
                "source_session": block.source_session,
            },
        )
    )
    if not ok:
        logger.warning("Qdrant index failed for block %s — degrade silently", block_id)
```

### Dependency diff

```diff
+ from src.shared import qdrant_search
+ from src.shared.search_types import IndexDocument
- # update() from sqlalchemy still used elsewhere in services.py — keep import
```

### Caller-side follow-up (out of scope for Phase 0.2 but flag-worthy)

`grep -n "update_embedding" core/src/modules/memvault/` to find callers. Most likely sites:
- `services.py` BaseCRUD `after_create` / `after_update` hooks
- Dream-loop / synthesis pipeline

Once Phase 1 confirms no caller relies on the `embedding` arg, drop it from the signature.

### Test plan

1. Create a block via `POST /api/memvault/blocks?space_id=test`
2. Call `await block_service.update_embedding(db, block.id, [0.0]*1024)`
3. Qdrant scroll: point exists with `entity_type=block`, `entity_id=<block.id>`, payload `content_preview` matches `block.content[:200]`
4. Update block content via `PATCH`, call `update_embedding` again → Qdrant point's `content_preview` reflects new content (UUIDv5 ensures upsert in place)
5. Call with non-existent block_id → raises `NotFoundError` (status 404)
6. Call with `embedding=[0.0]*100` → raises `BadRequestError` (dim guard still enforced)

---

## 5. `qdrant_search.scroll_by_service` helper (NEW)

Add to `core/src/shared/qdrant_search.py`. Sized at ~30 lines including docstring.

### Implementation

```python
async def scroll_by_service(
    service_id: str,
    *,
    entity_type: str | None = None,
    space_id: str | None = None,
    limit: int = 1000,
) -> set[str]:
    """Scroll all indexed entity_ids for a (service_id, entity_type?, space_id?) tuple.

    Returns a set of entity_ids currently present in Qdrant. Used by backfill /
    drift-detection routines to compute the anti-join against the source DB.

    Pages through Qdrant using the scroll API until next_page_offset is None.
    Returns empty set when Qdrant is unavailable (graceful degradation).

    Args:
        service_id:   "memvault" / "intelflow" / etc.
        entity_type:  optional filter — "block", "triple", etc.
        space_id:     optional filter — for tenant-scoped scans
        limit:        page size per scroll call (default 1000, Qdrant cap ~16k)

    Returns:
        set[str] of entity_ids; empty if collection empty or Qdrant down.
    """
    client = await qclient.get_client()
    if client is None:
        return set()

    must_conditions = [
        FieldCondition(key="service_id", match=MatchValue(value=service_id)),
    ]
    if entity_type:
        must_conditions.append(
            FieldCondition(key="entity_type", match=MatchValue(value=entity_type)),
        )
    if space_id:
        must_conditions.append(
            FieldCondition(key="space_id", match=MatchValue(value=space_id)),
        )
    scroll_filter = Filter(must=must_conditions)

    seen: set[str] = set()
    next_offset = None
    try:
        while True:
            points, next_offset = await client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=scroll_filter,
                limit=limit,
                offset=next_offset,
                with_payload=["entity_id"],
                with_vectors=False,
            )
            for p in points:
                eid = (p.payload or {}).get("entity_id")
                if eid:
                    seen.add(eid)
            if next_offset is None:
                break
        return seen
    except Exception as e:
        logger.error("scroll_by_service failed for %s: %s", service_id, e)
        return seen  # return partial result rather than blowing up backfill
```

### Dependency diff

No new imports — `qclient`, `FieldCondition`, `Filter`, `MatchValue`, `COLLECTION_NAME`, `logger` are all already in `qdrant_search.py` (verified L13-37).

### Test plan

1. Seed Qdrant: index 5 memvault triples + 3 memvault blocks via `index_document`
2. `await scroll_by_service("memvault")` → set of 8 entity_ids
3. `await scroll_by_service("memvault", entity_type="triple")` → set of 5 entity_ids
4. `await scroll_by_service("memvault", entity_type="triple", space_id="test")` → 5 (if all in `test`) or filtered subset
5. `await scroll_by_service("nonexistent")` → empty set
6. Stop Qdrant, retry → empty set, log warning, no exception
7. Seed 2500 entries (> default `limit=1000`) → verify pagination returns full 2500

---

## Summary

| Site | File:Line | Change kind |
|------|-----------|-------------|
| 1 | `kg_routes.py:74` | Replace SA write with `qdrant_search.index_document` |
| 2 | `kg_routes.py:500` | Replace `WHERE embedding IS NULL` with anti-join via `scroll_by_service` |
| 3 | `kg_routes.py:513` | Subsumed by #2 (`index_documents_batch`) |
| 4 | `services.py:995` | Re-fetch block, content-driven `index_document`; ignore `embedding` arg |
| 5 | `qdrant_search.py` (new) | Add `scroll_by_service` helper (~50 lines incl. docstring/error handling) |

After all five changes:

```bash
grep -rn "\.embedding\s*=" core/src/modules/memvault/    # → expect 0 hits
grep -rn "embedding=" core/src/modules/memvault/         # → expect 0 hits in routes/services
grep -rn "Triple.embedding" core/src/modules/memvault/   # → expect 0 hits
grep -rn "MemoryBlock.embedding" core/src/modules/memvault/  # → expect 0 hits
```
