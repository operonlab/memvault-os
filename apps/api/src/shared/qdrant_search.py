"""Unified Qdrant hybrid search SDK for all Workshop modules.

Provides: init_collection, index_document, index_documents_batch,
hybrid_search, delete_document, search_across_services.

Falls back to None/empty results when Qdrant is unavailable.
"""

import asyncio
import logging
import time

from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from . import qdrant_client as qclient
from .embedding import EMBEDDING_DIM, get_embedding, get_embeddings_batch
from .qdrant_client import get_qdrant_client
from .search_types import IndexDocument, SearchConfig, SearchMetadata, SearchResult
from .sparse_tokenizer import text_to_sparse_vector

logger = logging.getLogger(__name__)

COLLECTION_NAME = "workshop-docs-1024"

# Payload index field definitions
_PAYLOAD_INDEXES = {
    "service_id": PayloadSchemaType.KEYWORD,
    "entity_id": PayloadSchemaType.KEYWORD,
    "entity_type": PayloadSchemaType.KEYWORD,
    "space_id": PayloadSchemaType.KEYWORD,
    "tags": PayloadSchemaType.KEYWORD,
    "created_at": PayloadSchemaType.DATETIME,
}


async def init_collection() -> bool:
    """Create the workshop collection with dense + sparse vectors if it doesn't exist."""
    client = await qclient.get_client()
    if client is None:
        logger.warning("Qdrant unavailable — skipping collection init")
        return False

    try:
        collections = [c.name for c in (await client.get_collections()).collections]
        if COLLECTION_NAME in collections:
            logger.info("Collection %s already exists", COLLECTION_NAME)
            return True

        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                "dense": VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE,
                    on_disk=True,
                ),
            },
            sparse_vectors_config={
                "bm25": SparseVectorParams(
                    index=SparseIndexParams(on_disk=True),
                ),
            },
        )

        # Create payload indexes for efficient filtering
        for field_name, field_type in _PAYLOAD_INDEXES.items():
            await client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name=field_name,
                field_schema=field_type,
            )

        logger.info("Created collection %s with dense + sparse vectors", COLLECTION_NAME)
        return True
    except Exception as e:
        logger.error("Failed to create collection: %s", e)
        return False


async def index_document(doc: IndexDocument) -> bool:
    """Index a single document: embed + tokenize + upsert to Qdrant."""
    client = await qclient.get_client()
    if client is None:
        return False

    try:
        # Generate dense embedding
        embedding = await get_embedding(doc.content, task_type="search_document")
        if embedding is None:
            logger.warning("Failed to generate embedding for %s/%s", doc.service_id, doc.entity_id)
            return False

        # Generate sparse vector (BM25-like, per-service avgdl)
        sparse = text_to_sparse_vector(doc.content, service=doc.service_id)

        # Build point
        point_id = _entity_to_point_id(doc.service_id, doc.entity_id)
        payload = {
            "service_id": doc.service_id,
            "entity_id": doc.entity_id,
            "entity_type": doc.entity_type,
            "space_id": doc.space_id,
            "content_preview": doc.content[:200],
            "tags": doc.tags,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            **doc.metadata,
        }

        point = PointStruct(
            id=point_id,
            vector={
                "dense": embedding,
                "bm25": SparseVector(
                    indices=list(sparse.keys()),
                    values=list(sparse.values()),
                ),
            },
            payload=payload,
        )

        await client.upsert(collection_name=COLLECTION_NAME, points=[point])
        return True
    except Exception as e:
        logger.error("Failed to index document %s/%s: %s", doc.service_id, doc.entity_id, e)
        return False


async def index_documents_batch(docs: list[IndexDocument]) -> int:
    """Index multiple documents in batch. Returns count of successfully indexed."""
    client = await qclient.get_client()
    if client is None or not docs:
        return 0

    try:
        # Batch embed all contents
        contents = [doc.content for doc in docs]
        embeddings = await get_embeddings_batch(contents, task_type="search_document")

        points = []
        for doc, embedding in zip(docs, embeddings, strict=True):
            if embedding is None:
                continue

            sparse = text_to_sparse_vector(doc.content, service=doc.service_id)
            point_id = _entity_to_point_id(doc.service_id, doc.entity_id)

            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        "dense": embedding,
                        "bm25": SparseVector(
                            indices=list(sparse.keys()),
                            values=list(sparse.values()),
                        ),
                    },
                    payload={
                        "service_id": doc.service_id,
                        "entity_id": doc.entity_id,
                        "entity_type": doc.entity_type,
                        "space_id": doc.space_id,
                        "content_preview": doc.content[:200],
                        "tags": doc.tags,
                        "created_at": doc.created_at.isoformat() if doc.created_at else None,
                        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                        **doc.metadata,
                    },
                )
            )

        if points:
            # Upsert in batches of 100
            for i in range(0, len(points), 100):
                batch = points[i : i + 100]
                await client.upsert(collection_name=COLLECTION_NAME, points=batch)

        return len(points)
    except Exception as e:
        logger.error("Batch index failed: %s", e)
        return 0


async def hybrid_search(
    query: str,
    space_id: str,
    config: SearchConfig | None = None,
    *,
    with_vectors: bool = False,
) -> tuple[list[SearchResult], SearchMetadata]:
    """Execute hybrid search (sparse BM25 + dense semantic) with RRF fusion.

    Args:
        with_vectors: If True, request dense vectors from Qdrant and populate
            SearchResult.embedding.  Used by scoring pipelines that need
            SemanticBoostOp / MMROp (cosine similarity requires embeddings).
    """
    config = config or SearchConfig()
    meta = SearchMetadata()
    start = time.monotonic()

    client = await qclient.get_client()
    if client is None:
        meta.backend = "pgvector_fallback"
        return [], meta

    try:
        meta.collection = COLLECTION_NAME

        # Build filter
        must_conditions = [
            FieldCondition(key="space_id", match=MatchValue(value=space_id)),
        ]
        if config.service_ids:
            must_conditions.append(
                FieldCondition(key="service_id", match=MatchAny(any=config.service_ids)),
            )
        if config.tag_filter:
            must_conditions.append(
                FieldCondition(key="tags", match=MatchAny(any=config.tag_filter)),
            )

        query_filter = Filter(must=must_conditions)

        # Generate query vectors — start embedding as task, do sparse while waiting
        prefetch_list = []

        embedding_task = None
        if config.use_dense:
            embedding_task = asyncio.create_task(get_embedding(query, task_type="search_query"))

        if config.use_sparse:
            # Use first service_id for per-service avgdl if filtering single service
            query_service = None
            if config.service_ids and len(config.service_ids) == 1:
                query_service = config.service_ids[0]
            sparse = text_to_sparse_vector(query, service=query_service)
            if sparse:
                prefetch_list.append(
                    Prefetch(
                        query=SparseVector(
                            indices=list(sparse.keys()),
                            values=list(sparse.values()),
                        ),
                        using="bm25",
                        limit=config.top_k * 3,
                        filter=query_filter,
                    )
                )
                meta.sparse_used = True

        if embedding_task is not None:
            query_embedding = await embedding_task
            if query_embedding:
                prefetch_list.append(
                    Prefetch(
                        query=query_embedding,
                        using="dense",
                        limit=config.top_k * 3,
                        filter=query_filter,
                    )
                )
                meta.dense_used = True

        if not prefetch_list:
            return [], meta

        # Execute hybrid query with RRF fusion
        results = await client.query_points(
            collection_name=COLLECTION_NAME,
            prefetch=prefetch_list,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=config.top_k,
            with_payload=True,
            with_vectors=["dense"] if with_vectors else False,
        )

        # Convert to SearchResult
        search_results = []
        for point in results.points:
            payload = point.payload or {}
            score = point.score or 0.0

            if score < config.score_threshold:
                continue

            # Extract dense vector when requested (needed for SemanticBoostOp/MMROp)
            point_embedding: list[float] | None = None
            if with_vectors and isinstance(point.vector, dict):
                point_embedding = point.vector.get("dense")

            search_results.append(
                SearchResult(
                    entity_id=payload.get("entity_id", ""),
                    service_id=payload.get("service_id", ""),
                    entity_type=payload.get("entity_type", ""),
                    score=score,
                    content_preview=payload.get("content_preview", ""),
                    tags=payload.get("tags", []),
                    metadata={
                        k: v
                        for k, v in payload.items()
                        if k
                        not in {
                            "entity_id",
                            "service_id",
                            "entity_type",
                            "content_preview",
                            "tags",
                            "space_id",
                        }
                    },
                    embedding=point_embedding,
                )
            )

        meta.total_candidates = len(search_results)
        meta.query_time_ms = (time.monotonic() - start) * 1000

        return search_results, meta
    except Exception as e:
        logger.error("Hybrid search failed: %s", e)
        meta.backend = "pgvector_fallback"
        meta.query_time_ms = (time.monotonic() - start) * 1000
        return [], meta


async def vector_search(
    query_embedding: list[float],
    space_id: str,
    config: SearchConfig,
) -> list[SearchResult]:
    """Dense-only vector search with pre-computed embedding.

    Use when the caller already has an embedding vector and doesn't need
    BM25/sparse search. Returns empty list if Qdrant is unavailable.
    """
    client = await qclient.get_client()
    if client is None:
        return []

    try:
        # Build filter
        must_conditions = [
            FieldCondition(key="space_id", match=MatchValue(value=space_id)),
        ]
        if config.service_ids:
            must_conditions.append(
                FieldCondition(key="service_id", match=MatchAny(any=config.service_ids))
            )
        if config.tag_filter:
            must_conditions.append(
                FieldCondition(key="tags", match=MatchAny(any=config.tag_filter))
            )

        response = await client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embedding,
            using="dense",
            query_filter=Filter(must=must_conditions),
            limit=config.top_k,
            with_payload=True,
        )

        search_results = []
        for point in response.points:
            payload = point.payload or {}
            score = point.score or 0.0
            if config.score_threshold and score < config.score_threshold:
                continue
            search_results.append(
                SearchResult(
                    entity_id=payload.get("entity_id", ""),
                    service_id=payload.get("service_id", ""),
                    entity_type=payload.get("entity_type", ""),
                    score=score,
                    content_preview=payload.get("content_preview", ""),
                    tags=payload.get("tags", []),
                )
            )
        return search_results
    except Exception as e:
        logger.error("Qdrant vector_search failed: %s", e)
        return []


async def delete_document(service_id: str, entity_id: str) -> bool:
    """Delete a document from Qdrant by service_id + entity_id."""
    client = await qclient.get_client()
    if client is None:
        return False

    try:
        point_id = _entity_to_point_id(service_id, entity_id)
        await client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=[point_id],
        )
        return True
    except Exception as e:
        logger.error("Failed to delete %s/%s: %s", service_id, entity_id, e)
        return False


async def search_across_services(
    query: str,
    space_id: str,
    service_ids: list[str] | None = None,
    top_k: int = 10,
) -> tuple[list[SearchResult], SearchMetadata]:
    """Cross-module search — search across multiple (or all) services."""
    config = SearchConfig(
        top_k=top_k,
        service_ids=service_ids,
    )
    return await hybrid_search(query, space_id, config)


def _entity_to_point_id(service_id: str, entity_id: str) -> str:
    """Generate a deterministic Qdrant point ID from service + entity.

    Uses UUID v5 (namespace + name) for deterministic, collision-free IDs.
    """
    import uuid

    namespace = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    return str(uuid.uuid5(namespace, f"{service_id}:{entity_id}"))


async def delete_by_service_and_space(service_id: str, space_id: str) -> int:
    """Delete all Qdrant points matching a service_id + space_id filter.

    Used for atomic re-indexing: delete old → upsert new.
    Returns 1 on success, 0 if unavailable.
    """
    client = await qclient.get_client()
    if client is None:
        return 0

    try:
        await client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[
                    FieldCondition(key="service_id", match=MatchValue(value=service_id)),
                    FieldCondition(key="space_id", match=MatchValue(value=space_id)),
                ]
            ),
        )
        logger.info("Deleted Qdrant points for service=%s space=%s", service_id, space_id)
        return 1
    except Exception as e:
        logger.error("Failed to delete points for %s/%s: %s", service_id, space_id, e)
        return 0


async def search_with_fallback(
    query: str,
    space_id: str,
    service_id: str,
    *,
    top_k: int = 20,
    entity_type_filter: str | None = None,
    tag_filter: list[str] | None = None,
) -> tuple[list["SearchResult"], "SearchMetadata"]:
    """Qdrant hybrid search with graceful ILIKE fallback signal.

    Wraps hybrid_search() with availability check and entity_type filtering.
    When Qdrant is unavailable or returns empty results, returns empty list
    with meta.backend = "ilike_fallback" — caller should handle ILIKE themselves.

    Returns:
        (results, metadata) — results may be empty if fallback needed.
    """
    from .qdrant_client import is_available as qdrant_available

    meta = SearchMetadata(backend="ilike_fallback")

    if not await qdrant_available():
        return [], meta

    config = SearchConfig(top_k=top_k, service_ids=[service_id], tag_filter=tag_filter)
    results, meta = await hybrid_search(query, space_id, config)

    if not results:
        meta.backend = "ilike_fallback"
        return [], meta

    if entity_type_filter:
        results = [r for r in results if r.entity_type == entity_type_filter]

    return results, meta


async def scroll_by_service(
    service_id: str,
    entity_type: str | None = None,
    space_id: str | None = None,
    limit: int = 1000,
) -> set[str]:
    """List entity_id of all docs indexed under service_id (+ optional filters).

    Used by memvault kg_routes.py:528 backfill_embeddings to anti-join Postgres
    Triple table — find triples without Qdrant index.
    """
    client = get_qdrant_client()
    if client is None:
        return set()

    must = [FieldCondition(key="service_id", match=MatchValue(value=service_id))]
    if entity_type:
        must.append(FieldCondition(key="entity_type", match=MatchValue(value=entity_type)))
    if space_id:
        must.append(FieldCondition(key="space_id", match=MatchValue(value=space_id)))

    result: set[str] = set()
    offset = None
    page_size = min(limit, 256)
    while True:
        try:
            points, offset = await client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=Filter(must=must),
                limit=page_size,
                with_payload=["entity_id"],
                with_vectors=False,
                offset=offset,
            )
        except Exception as e:
            logger.warning("scroll_by_service failed for %s: %s", service_id, e)
            break
        for p in points:
            payload = p.payload or {}
            eid = payload.get("entity_id")
            if eid:
                result.add(eid)
        if offset is None or len(result) >= limit:
            break
    return result
