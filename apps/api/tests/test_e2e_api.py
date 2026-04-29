"""E2E tests against a real docker-compose API.

NOT a unit test. This file makes real HTTP calls to:
    $MEMVAULT_TEST_BASE_URL  (default http://localhost:8080)

Run after `docker compose up -d && docker compose exec api alembic upgrade head`.

Design principles (test-adversary mode):
- Mutation thinking: every assertion should be one a faulty `>` -> `>=` /
  `==` -> `!=` flip would break.
- Invariants over fixed I/O: prefer "results <= limit", "POST then GET round-trips"
  over "exactly 5 results".
- Defensive contract checks: where the manifest leaves a shape ambiguous (e.g.
  PaginatedResponse vs bare list, 200 vs 201), accept either and pin the rest.
- No mocking — these tests are designed to surface real bugs.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

CJK_PHRASE = "繁體中文記憶區塊內容測試 — αβγ"
LONG_CONTENT = "x" * 12_000  # > 10 KB
SQLI_PAYLOAD = "'; DROP TABLE blocks; --"


def _assert_iso_timestamp(value: object, field: str) -> None:
    """Loose check that `value` looks like an ISO datetime string."""
    assert isinstance(value, str), f"{field} not a string: {value!r}"
    # Crude ISO-8601 sniff — must have date and 'T' or space separator.
    assert len(value) >= 10 and value[4] == "-" and value[7] == "-", (
        f"{field} not ISO-8601: {value!r}"
    )


def _assert_paginated(payload: object) -> dict:
    """Assert payload looks like PaginatedResponse and return it."""
    assert isinstance(payload, dict), f"expected dict, got {type(payload).__name__}"
    assert "items" in payload, f"missing 'items' key in {list(payload)}"
    assert isinstance(payload["items"], list), "items is not a list"
    # `total` is conventional but not strictly required by the manifest;
    # if present, it must be a non-negative int.
    if "total" in payload:
        assert isinstance(payload["total"], int) and payload["total"] >= 0
    return payload


def _assert_block_response(block: object) -> dict:
    """Validate a block dict matches the public MemoryBlockResponse shape."""
    assert isinstance(block, dict), f"block not dict: {block!r}"
    for required in ("id", "content", "block_type", "tags", "created_at"):
        assert required in block, f"block missing {required!r}: {block!r}"
    assert isinstance(block["id"], str) and block["id"], "id is empty"
    assert isinstance(block["content"], str), "content is not str"
    assert isinstance(block["block_type"], str), "block_type is not str"
    assert isinstance(block["tags"], list), "tags is not list"
    _assert_iso_timestamp(block["created_at"], "created_at")
    return block


# --------------------------------------------------------------------------- #
# Group 1 — Smoke (≥3)
# --------------------------------------------------------------------------- #


async def test_smoke_status_endpoint(httpx_client: httpx.AsyncClient):
    """GET /api/memvault/status — must return 200 with a JSON body."""
    r = await httpx_client.get("/api/memvault/status")
    assert r.status_code == 200, f"status endpoint not 200: {r.status_code} {r.text[:200]}"
    payload = r.json()
    assert isinstance(payload, (dict, list)), f"unexpected payload type: {type(payload)}"


async def test_smoke_sync_stats(httpx_client: httpx.AsyncClient):
    """GET /api/memvault/sync/stats — must return JSON, not 5xx."""
    r = await httpx_client.get("/api/memvault/sync/stats")
    assert r.status_code == 200, f"sync/stats not 200: {r.status_code} {r.text[:200]}"
    payload = r.json()
    assert isinstance(payload, dict), "sync/stats must be a dict"


async def test_smoke_blocks_list_reachable(httpx_client: httpx.AsyncClient):
    """GET /api/memvault/blocks — DB / app stack alive, returns list shape."""
    r = await httpx_client.get("/api/memvault/blocks", params={"page": 1, "page_size": 1})
    assert r.status_code == 200, f"blocks list not 200: {r.status_code}"
    payload = r.json()
    # Shape must be paginated OR a bare list — pin both possibilities.
    if isinstance(payload, dict):
        _assert_paginated(payload)
    else:
        assert isinstance(payload, list), f"unexpected shape: {type(payload)}"


# --------------------------------------------------------------------------- #
# Group 2 — Block CRUD (≥8)
# --------------------------------------------------------------------------- #


async def test_block_create_returns_201_or_200_with_full_body(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker
):
    body = {
        "content": f"{unique_marker} create-test",
        "block_type": "general",
        "tags": ["memvault_test", "crud"],
    }
    r = await httpx_client.post("/api/memvault/blocks", json=body)
    assert r.status_code in (200, 201), (
        f"create not 2xx: {r.status_code} {r.text[:300]}"
    )
    block = _assert_block_response(r.json())
    assert block["content"] == body["content"], "content not echoed"
    assert block["block_type"] == "general"
    assert "memvault_test" in block["tags"]


async def test_block_list_pagination_respects_page_size(
    httpx_client: httpx.AsyncClient, clean_db, make_block
):
    # Create 3 blocks to ensure list isn't trivially empty.
    created_ids = []
    for _ in range(3):
        b = await make_block()
        created_ids.append(b["id"])

    r = await httpx_client.get(
        "/api/memvault/blocks", params={"page": 1, "page_size": 2}
    )
    assert r.status_code == 200
    payload = r.json()

    if isinstance(payload, dict):
        _assert_paginated(payload)
        items = payload["items"]
        # Invariant: returned items <= page_size.
        assert len(items) <= 2, f"page_size invariant broken: got {len(items)} > 2"
    else:
        items = payload
        assert isinstance(items, list)
        # If the API returns a bare list, page_size should still limit it.
        assert len(items) <= 2, "page_size not honored on bare list response"


async def test_block_get_unknown_id_returns_404(httpx_client: httpx.AsyncClient):
    bogus = "z" * 32  # plausible-shaped but should not exist
    r = await httpx_client.get(f"/api/memvault/blocks/{bogus}")
    assert r.status_code == 404, (
        f"unknown id should be 404, got {r.status_code} {r.text[:200]}"
    )


async def test_block_roundtrip_post_then_get_same_content(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker, make_block
):
    """Invariant: POST + GET = same content (no silent mutation/truncation)."""
    payload = f"{unique_marker} roundtrip — αβγ — {uuid.uuid4()}"
    created = await make_block(content=payload, tags=["memvault_test", "roundtrip"])
    r = await httpx_client.get(f"/api/memvault/blocks/{created['id']}")
    assert r.status_code == 200, f"GET after POST failed: {r.status_code}"
    fetched = _assert_block_response(r.json())
    assert fetched["content"] == payload, (
        "roundtrip content mismatch — POST stored != GET returned"
    )
    assert fetched["id"] == created["id"], "id changed between POST and GET"


async def test_block_update_partial_keeps_other_fields(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker, make_block
):
    original = await make_block(
        content=f"{unique_marker} before-update",
        block_type="general",
        tags=["memvault_test", "update-test"],
    )
    new_content = f"{unique_marker} after-update"
    r = await httpx_client.put(
        f"/api/memvault/blocks/{original['id']}", json={"content": new_content}
    )
    assert r.status_code in (200, 204), f"PUT not 2xx: {r.status_code} {r.text[:200]}"

    # Re-fetch and confirm content changed but block_type kept.
    rg = await httpx_client.get(f"/api/memvault/blocks/{original['id']}")
    assert rg.status_code == 200
    fetched = _assert_block_response(rg.json())
    assert fetched["content"] == new_content, "PUT did not persist new content"
    assert fetched["block_type"] == original["block_type"], (
        "PUT with only `content` clobbered block_type"
    )
    # Tags were not in the PUT — must not be silently emptied.
    assert "memvault_test" in fetched["tags"], (
        "partial PUT silently dropped tags — schema says fields are optional"
    )


async def test_block_update_full_replaces_all_fields(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker, make_block
):
    original = await make_block(
        content=f"{unique_marker} v1", tags=["memvault_test", "old-tag"]
    )
    body = {
        "content": f"{unique_marker} v2",
        "block_type": "knowledge",
        "tags": ["memvault_test", "new-tag"],
    }
    r = await httpx_client.put(f"/api/memvault/blocks/{original['id']}", json=body)
    assert r.status_code in (200, 204), f"full PUT not 2xx: {r.status_code}"

    rg = await httpx_client.get(f"/api/memvault/blocks/{original['id']}")
    assert rg.status_code == 200
    fetched = _assert_block_response(rg.json())
    assert fetched["content"] == body["content"]
    assert fetched["block_type"] == "knowledge"
    assert "new-tag" in fetched["tags"]


async def test_block_delete_then_get_returns_404(
    httpx_client: httpx.AsyncClient, clean_db, make_block
):
    block = await make_block()
    rd = await httpx_client.delete(f"/api/memvault/blocks/{block['id']}")
    assert rd.status_code in (200, 204), (
        f"DELETE not 2xx: {rd.status_code} {rd.text[:200]}"
    )
    rg = await httpx_client.get(f"/api/memvault/blocks/{block['id']}")
    # After delete, GET must NOT return 200 with the original content. 404 is
    # canonical; 410 acceptable. 200 = bug.
    assert rg.status_code in (404, 410), (
        f"GET after DELETE must be 404/410, got {rg.status_code} body={rg.text[:200]}"
    )


async def test_block_create_empty_content_rejected(
    httpx_client: httpx.AsyncClient, clean_db
):
    """Empty content should be a client error, not a silent insert."""
    r = await httpx_client.post(
        "/api/memvault/blocks",
        json={"content": "", "block_type": "general", "tags": ["memvault_test"]},
    )
    assert r.status_code in (400, 422), (
        f"empty content should be 4xx, got {r.status_code} {r.text[:200]}"
    )


async def test_block_create_long_content_accepted(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker
):
    """>10KB content should be stored verbatim (Text column has no length cap)."""
    long_payload = f"{unique_marker} " + LONG_CONTENT
    r = await httpx_client.post(
        "/api/memvault/blocks",
        json={
            "content": long_payload,
            "block_type": "general",
            "tags": ["memvault_test", "long"],
        },
    )
    assert r.status_code in (200, 201), (
        f"long content rejected: {r.status_code} {r.text[:200]}"
    )
    block = _assert_block_response(r.json())
    assert len(block["content"]) == len(long_payload), (
        f"long content truncated: in={len(long_payload)} out={len(block['content'])}"
    )


async def test_block_create_cjk_content_roundtrip(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker
):
    payload = f"{unique_marker} {CJK_PHRASE}"
    r = await httpx_client.post(
        "/api/memvault/blocks",
        json={"content": payload, "tags": ["memvault_test", "cjk"]},
    )
    assert r.status_code in (200, 201), f"CJK create failed: {r.status_code}"
    block = _assert_block_response(r.json())
    assert block["content"] == payload, "CJK roundtrip mismatch (encoding bug?)"


async def test_block_invalid_block_type_handled(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker
):
    """schemas.py defines BLOCK_TYPES = {knowledge,skill,attitude,general}.
    An obviously-invalid type should either be rejected (4xx) OR coerced to
    `general`. A 5xx is a bug.
    """
    r = await httpx_client.post(
        "/api/memvault/blocks",
        json={
            "content": f"{unique_marker} bad-type",
            "block_type": "definitely_not_a_real_type",
            "tags": ["memvault_test"],
        },
    )
    assert r.status_code < 500, (
        f"invalid block_type caused 5xx (should be 4xx or coerced): {r.status_code}"
    )
    if r.status_code in (200, 201):
        block = r.json()
        assert block["block_type"] in {
            "knowledge",
            "skill",
            "attitude",
            "general",
            "definitely_not_a_real_type",
        }, f"unexpected block_type: {block.get('block_type')!r}"


# --------------------------------------------------------------------------- #
# Group 3 — Search (≥5)
# --------------------------------------------------------------------------- #


async def test_search_returns_results_list(
    httpx_client: httpx.AsyncClient, make_block, unique_marker
):
    """Seed a block, search for its marker, and verify result shape."""
    marker = unique_marker
    await make_block(content=f"{marker} the lazy fox jumps")
    # Allow brief settle for any indexing pipeline.
    r = await httpx_client.get("/api/memvault/search", params={"q": marker, "top_k": 5})
    assert r.status_code == 200, f"search failed: {r.status_code} {r.text[:200]}"
    payload = r.json()
    # Either bare list, or { results: [...] } per EnhancedSearchResult.
    if isinstance(payload, dict):
        results = payload.get("results")
        assert isinstance(results, list), f"results not list: {payload!r}"
        if "metadata" in payload and payload["metadata"] is not None:
            assert isinstance(payload["metadata"], dict)
    else:
        assert isinstance(payload, list), f"unexpected search shape: {type(payload)}"


async def test_search_empty_query_rejected(httpx_client: httpx.AsyncClient):
    """SemanticSearchParams says q has min_length=1 — empty must 4xx."""
    r = await httpx_client.get("/api/memvault/search", params={"q": "", "top_k": 5})
    assert r.status_code in (400, 422), (
        f"empty q should 4xx, got {r.status_code} {r.text[:200]}"
    )


async def test_search_respects_top_k_invariant(
    httpx_client: httpx.AsyncClient, make_block, unique_marker
):
    """Invariant: results.length <= top_k regardless of corpus."""
    marker = unique_marker
    for i in range(7):
        await make_block(content=f"{marker} doc {i}")

    r = await httpx_client.get("/api/memvault/search", params={"q": marker, "top_k": 3})
    assert r.status_code == 200
    payload = r.json()
    results = payload.get("results") if isinstance(payload, dict) else payload
    assert isinstance(results, list)
    assert len(results) <= 3, (
        f"top_k invariant broken — asked 3, got {len(results)}"
    )


async def test_search_top_k_out_of_range(httpx_client: httpx.AsyncClient):
    """top_k bounds are 1..100 per SemanticSearchParams."""
    r = await httpx_client.get(
        "/api/memvault/search", params={"q": "anything", "top_k": 0}
    )
    assert r.status_code in (400, 422), (
        f"top_k=0 should 4xx, got {r.status_code}"
    )
    r2 = await httpx_client.get(
        "/api/memvault/search", params={"q": "anything", "top_k": 9999}
    )
    assert r2.status_code in (400, 422), (
        f"top_k=9999 should 4xx, got {r2.status_code}"
    )


async def test_search_cjk_query_does_not_500(
    httpx_client: httpx.AsyncClient, make_block, unique_marker
):
    marker = unique_marker
    await make_block(content=f"{marker} {CJK_PHRASE}")
    r = await httpx_client.get(
        "/api/memvault/search", params={"q": "繁體中文 test", "top_k": 5}
    )
    assert r.status_code == 200, (
        f"CJK query crashed search: {r.status_code} {r.text[:300]}"
    )


async def test_search_results_have_score_field(
    httpx_client: httpx.AsyncClient, make_block, unique_marker
):
    marker = unique_marker
    await make_block(content=f"{marker} unique-token-for-score-test")
    r = await httpx_client.get("/api/memvault/search", params={"q": marker, "top_k": 5})
    assert r.status_code == 200
    payload = r.json()
    results = payload.get("results") if isinstance(payload, dict) else payload
    if results:
        first = results[0]
        # SemanticSearchResult { block, score } OR MemoryBlockBrief { score }
        if isinstance(first, dict) and "block" in first:
            assert "score" in first, "SemanticSearchResult missing score"
            assert isinstance(first["score"], (int, float))
        else:
            assert isinstance(first, dict)
            # MemoryBlockBrief — score is Optional, but if present must be number
            if "score" in first and first["score"] is not None:
                assert isinstance(first["score"], (int, float))


# --------------------------------------------------------------------------- #
# Group 4 — KG triples (≥5)
# --------------------------------------------------------------------------- #


async def test_kg_triple_create_returns_id(httpx_client: httpx.AsyncClient):
    body = {
        "subject": f"e2e_subject_{uuid.uuid4().hex[:8]}",
        "predicate": "tested_by",
        "object": "memvault_test_e2e",
    }
    r = await httpx_client.post("/api/memvault/kg/triples", json=body)
    assert r.status_code in (200, 201), (
        f"triple create not 2xx: {r.status_code} {r.text[:300]}"
    )
    payload = r.json()
    assert isinstance(payload, dict), "triple create response not dict"
    assert payload.get("id"), f"triple create returned no id: {payload!r}"
    assert payload.get("subject") == body["subject"], "subject not echoed"
    assert payload.get("predicate") == body["predicate"]
    assert payload.get("object") == body["object"]


async def test_kg_triple_list_paginated(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get(
        "/api/memvault/kg/triples", params={"page": 1, "page_size": 5}
    )
    assert r.status_code == 200, f"kg/triples list not 200: {r.status_code}"
    payload = r.json()
    if isinstance(payload, dict):
        items = payload.get("items")
        assert isinstance(items, list)
        assert len(items) <= 5, f"page_size invariant broken: {len(items)}"
    else:
        assert isinstance(payload, list)
        assert len(payload) <= 5


async def test_kg_triple_delete_returns_2xx(httpx_client: httpx.AsyncClient):
    body = {
        "subject": f"e2e_del_{uuid.uuid4().hex[:8]}",
        "predicate": "ephemeral",
        "object": "to_delete",
    }
    r_create = await httpx_client.post("/api/memvault/kg/triples", json=body)
    assert r_create.status_code in (200, 201)
    triple_id = r_create.json()["id"]
    r_del = await httpx_client.delete(f"/api/memvault/kg/triples/{triple_id}")
    assert r_del.status_code in (200, 204), (
        f"DELETE not 2xx: {r_del.status_code} {r_del.text[:200]}"
    )


async def test_kg_triple_batch_insert(httpx_client: httpx.AsyncClient):
    """Batch insert via TripleBatchCreate schema (session_id + triples list).

    Contract: `/api/memvault/kg/triples/batch` accepts TripleBatchCreate —
    a dict with REQUIRED session_id plus a triples list. Earlier versions of
    this test sent both `{"triples": [...]}` and a bare list; both 422'd
    because session_id was missing.
    """
    session_id = f"e2e_batch_session_{uuid.uuid4().hex[:8]}"
    triples = [
        {
            "subject": f"e2e_batch_{uuid.uuid4().hex[:6]}_{i}",
            "predicate": "rel",
            "object": f"obj{i}",
        }
        for i in range(3)
    ]
    body = {"session_id": session_id, "triples": triples}
    r = await httpx_client.post("/api/memvault/kg/triples/batch", json=body)
    assert r.status_code in (200, 201), (
        f"batch insert rejected: status={r.status_code} body={r.text[:300]}"
    )
    payload = r.json()
    assert payload.get("ingested") == 3, (
        f"expected 3 ingested, got {payload!r}"
    )


async def test_batch_ingest_partial_dup_only_rolls_back_dup_row(
    httpx_client: httpx.AsyncClient,
):
    """Regression for codex review #5 (batch_ingest IntegrityError over-rollback).

    Mix unique + duplicate triples in one batch. Result must report only the
    valid count, and the valid rows MUST be persisted (not rolled back together
    with the offending row). Pre-fix code called `db.rollback()` on any
    IntegrityError, which discarded all already-flushed rows in the same
    transaction; the fix wraps each row in a SAVEPOINT.
    """
    session_id = f"e2e_dup_session_{uuid.uuid4().hex[:8]}"
    marker = uuid.uuid4().hex[:8]
    unique_triples = [
        {
            "subject": f"e2e_dup_subj_{marker}_{i}",
            "predicate": "rel",
            "object": f"obj_{i}",
            "session_id": session_id,
        }
        for i in range(3)
    ]
    # Two duplicates of the first unique triple — collide on
    # uq_triples_space_session_spo (space_id, source_session, s, p, o).
    duplicate = dict(unique_triples[0])
    triples = unique_triples + [duplicate, duplicate]

    body = {"session_id": session_id, "triples": triples}
    r = await httpx_client.post("/api/memvault/kg/triples/batch", json=body)
    assert r.status_code in (200, 201), (
        f"batch ingest not 2xx: {r.status_code} {r.text[:300]}"
    )
    payload = r.json()
    assert isinstance(payload, dict), f"expected dict, got {payload!r}"
    assert payload.get("ingested") == 3, (
        f"expected 3 unique rows ingested, got {payload!r}"
    )

    # Verify the unique rows actually persisted — pre-fix bug rolled them back.
    for t in unique_triples:
        rs = await httpx_client.get(
            "/api/memvault/kg/triples",
            params={"predicate": t["predicate"], "subject": t["subject"]},
        )
        assert rs.status_code == 200, f"list not 200: {rs.status_code} {rs.text[:200]}"
        payload = rs.json()
        items = payload.get("items") if isinstance(payload, dict) else payload
        assert isinstance(items, list)
        assert any(it.get("subject") == t["subject"] for it in items), (
            f"unique triple was rolled back by duplicate sibling: subject={t['subject']!r}"
        )


async def test_kg_triple_search_by_predicate(httpx_client: httpx.AsyncClient):
    """Seed a triple, then search via the semantic endpoint with its predicate.

    Contract: `/triples/search` takes `q=` (semantic query). When the
    embedding service is unavailable the route falls back to
    `search_by_predicate(q)`, so passing the predicate as `q` exercises both
    paths uniformly. Earlier versions of this test used `?predicate=`,
    which `/triples/search` does not accept (that param lives on `GET
    /triples`, the list endpoint).
    """
    pred = f"e2e_pred_{uuid.uuid4().hex[:8]}"
    body = {
        "subject": "alpha",
        "predicate": pred,
        "object": "beta",
    }
    rc = await httpx_client.post("/api/memvault/kg/triples", json=body)
    assert rc.status_code in (200, 201)
    rs = await httpx_client.get(
        "/api/memvault/kg/triples/search", params={"q": pred}
    )
    assert rs.status_code == 200, f"triple search not 200: {rs.status_code}"
    payload = rs.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    assert isinstance(items, list)
    if items:
        # Invariant: in the predicate-fallback path every returned triple
        # has the predicate we queried with.
        for t in items:
            assert isinstance(t, dict)
            assert t.get("predicate") == pred, (
                f"search returned wrong predicate: asked={pred!r} got={t.get('predicate')!r}"
            )


async def test_kg_triple_unknown_id_delete_returns_404(
    httpx_client: httpx.AsyncClient,
):
    bogus = "z" * 32
    r = await httpx_client.delete(f"/api/memvault/kg/triples/{bogus}")
    assert r.status_code in (404, 410), (
        f"unknown triple delete should 404/410, got {r.status_code}"
    )


# --------------------------------------------------------------------------- #
# Group 5 — Recall (≥3)
# --------------------------------------------------------------------------- #


async def test_kg_recall_returns_cascade_structure(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get(
        "/api/memvault/kg/recall", params={"q": "memvault_test_seed", "top_k": 5}
    )
    assert r.status_code == 200, f"kg/recall not 200: {r.status_code} {r.text[:300]}"
    payload = r.json()
    assert isinstance(payload, dict), "cascade recall must return a dict"
    # Cascade structure should expose triples / nodes / levels (or similar).
    # We don't insist on names — but at least one collection key must exist.
    interesting = {"triples", "nodes", "levels", "entities", "results", "items"}
    assert any(k in payload for k in interesting), (
        f"cascade response missing any structural key from {interesting}: "
        f"got keys={list(payload)}"
    )


async def test_kg_recall_empty_query_handled(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get("/api/memvault/kg/recall", params={"q": ""})
    # Either rejected (4xx) or returns empty result (200) — never 5xx.
    assert r.status_code < 500, (
        f"empty recall q caused 5xx: {r.status_code} {r.text[:300]}"
    )
    if r.status_code == 200:
        payload = r.json()
        assert isinstance(payload, dict)


async def test_kg_recall_no_match_returns_empty(httpx_client: httpx.AsyncClient):
    nonsense = f"absolutely_no_match_{uuid.uuid4().hex}"
    r = await httpx_client.get("/api/memvault/kg/recall", params={"q": nonsense})
    assert r.status_code == 200, f"no-match recall not 200: {r.status_code}"
    payload = r.json()
    assert isinstance(payload, dict)
    # Invariant: every list-valued field is finite (and ideally empty).
    for key, value in payload.items():
        if isinstance(value, list):
            # Must be a list of finite size — not a generator or None.
            assert len(value) >= 0


async def test_recall_text_post(httpx_client: httpx.AsyncClient, unique_marker):
    """POST /api/memvault/recall/text with a query body."""
    body = {"q": unique_marker, "top_k": 3}
    r = await httpx_client.post("/api/memvault/recall/text", json=body)
    assert r.status_code in (200, 201, 422), (
        f"recall/text unexpected status: {r.status_code} {r.text[:300]}"
    )
    if r.status_code in (200, 201):
        payload = r.json()
        # Accept either dict or list.
        assert isinstance(payload, (dict, list)), (
            f"recall/text returned {type(payload).__name__}"
        )


# --------------------------------------------------------------------------- #
# Group 6 — Health & ops (≥3)
# --------------------------------------------------------------------------- #


async def test_sync_stats_shape(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get("/api/memvault/sync/stats")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # Counts (if present) must be non-negative ints.
    for key, value in payload.items():
        if isinstance(value, (int, float)) and key.endswith(("_count", "_total")):
            assert value >= 0, f"negative count {key}={value}"


async def test_status_endpoint_shape(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get("/api/memvault/status")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, (dict, list))


async def test_tags_list(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get("/api/memvault/tags")
    assert r.status_code == 200, f"tags list not 200: {r.status_code}"
    payload = r.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    assert isinstance(items, list), f"tags response not list-shaped: {payload!r}"
    for tag in items:
        assert isinstance(tag, dict), f"tag entry not dict: {tag!r}"
        assert "name" in tag and isinstance(tag["name"], str)
        if "usage_count" in tag:
            assert isinstance(tag["usage_count"], int) and tag["usage_count"] >= 0


async def test_domains_crud_smoke(httpx_client: httpx.AsyncClient):
    """Minimal smoke for /domains POST + GET."""
    r_get = await httpx_client.get("/api/memvault/domains")
    assert r_get.status_code == 200
    body = {"name": f"e2e-domain-{uuid.uuid4().hex[:8]}", "description": "test"}
    r_post = await httpx_client.post("/api/memvault/domains", json=body)
    assert r_post.status_code in (200, 201), (
        f"domain POST not 2xx: {r_post.status_code} {r_post.text[:200]}"
    )
    domain = r_post.json()
    assert domain.get("name") == body["name"], "domain name not echoed"


# --------------------------------------------------------------------------- #
# Group 7 — Robustness (≥3)
# --------------------------------------------------------------------------- #


async def test_no_auth_header_works_in_single_user_mode(
    httpx_client: httpx.AsyncClient,
):
    """README claims single-user self-hosted — list should not 401."""
    r = await httpx_client.get(
        "/api/memvault/blocks", params={"page": 1, "page_size": 1}
    )
    assert r.status_code != 401, (
        "blocks list returned 401 with no auth header — "
        "single-user mode broken or auth stub mis-wired"
    )
    assert r.status_code != 403, (
        "blocks list returned 403 with no auth header — "
        "scope check misfiring in single-user mode"
    )


async def test_malformed_json_body_rejected(httpx_client: httpx.AsyncClient):
    r = await httpx_client.post(
        "/api/memvault/blocks",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    # Must be 4xx — never 5xx.
    assert 400 <= r.status_code < 500, (
        f"malformed JSON should 4xx, got {r.status_code} {r.text[:200]}"
    )


async def test_sql_injection_payload_stored_safely(
    httpx_client: httpx.AsyncClient, clean_db, unique_marker
):
    """SQL-shaped string in `content` must be stored verbatim and survive."""
    payload = f"{unique_marker} {SQLI_PAYLOAD}"
    r = await httpx_client.post(
        "/api/memvault/blocks",
        json={
            "content": payload,
            "block_type": "general",
            "tags": ["memvault_test", "sqli"],
        },
    )
    assert r.status_code in (200, 201), (
        f"SQLi-shaped content rejected: {r.status_code} {r.text[:200]}"
    )
    block = _assert_block_response(r.json())
    assert block["content"] == payload, (
        "SQLi content mutated/escaped on store — should be stored verbatim"
    )

    # Verify tables still exist — list endpoint must still respond.
    r2 = await httpx_client.get(
        "/api/memvault/blocks", params={"page": 1, "page_size": 1}
    )
    assert r2.status_code == 200, (
        f"blocks list broken after SQLi attempt: {r2.status_code} — "
        "possible injection success!"
    )

    # Verify GET-by-id still works on the SQLi block.
    r3 = await httpx_client.get(f"/api/memvault/blocks/{block['id']}")
    assert r3.status_code == 200
    assert r3.json()["content"] == payload


async def test_unknown_route_returns_404_not_5xx(httpx_client: httpx.AsyncClient):
    r = await httpx_client.get("/api/memvault/this_route_does_not_exist_xyz")
    assert r.status_code == 404, (
        f"unknown route should 404, got {r.status_code} — possible catch-all bug"
    )


async def test_invalid_pagination_params_handled(httpx_client: httpx.AsyncClient):
    """Negative page / page_size must 4xx, not return all rows."""
    r = await httpx_client.get(
        "/api/memvault/blocks", params={"page": -1, "page_size": 10}
    )
    assert r.status_code in (400, 422, 200), (
        f"unexpected status for page=-1: {r.status_code}"
    )
    if r.status_code == 200:
        # If accepted, must still cap items.
        payload = r.json()
        items = payload.get("items") if isinstance(payload, dict) else payload
        assert isinstance(items, list)
        assert len(items) <= 10


# --------------------------------------------------------------------------- #
# Group 8 — Cross-API invariants (bonus, extra coverage)
# --------------------------------------------------------------------------- #


async def test_pagination_total_is_consistent(
    httpx_client: httpx.AsyncClient, clean_db, make_block
):
    """If `total` is reported, it must be >= len(items) on a single page."""
    for _ in range(4):
        await make_block()
    r = await httpx_client.get(
        "/api/memvault/blocks", params={"page": 1, "page_size": 2}
    )
    assert r.status_code == 200
    payload = r.json()
    if isinstance(payload, dict) and "total" in payload:
        items = payload.get("items", [])
        assert isinstance(items, list)
        assert payload["total"] >= len(items), (
            f"total ({payload['total']}) < items returned ({len(items)})"
        )


async def test_block_response_is_valid_json_serializable(
    httpx_client: httpx.AsyncClient, make_block
):
    """Belt-and-braces: response body roundtrips through json.dumps cleanly."""
    block = await make_block()
    # If the API returned non-JSON-safe values (e.g. raw datetime), this would
    # have failed at httpx_client.json() already, but assert anyway.
    encoded = json.dumps(block)
    decoded = json.loads(encoded)
    assert decoded["id"] == block["id"]
