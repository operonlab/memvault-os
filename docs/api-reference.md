# memvault-os — API Reference

Auto-generated from [`docs/route_manifest.yaml`](./route_manifest.yaml) by `scripts/build-api-docs.py`. Total routes: **66**.

All routes are mounted under the api container (host port `${API_PORT:-8080}`). Auth in v1 single-user mode is a stub — every request runs as the lone owner. The `scope` column reflects the permission token an embedded multi-user deployment would enforce.

## Sections

- [Memory Blocks](#memory-blocks) — 5 route(s)
- [Sessions](#sessions) — 1 route(s)
- [Search & Query](#search-query) — 4 route(s)
- [Cascade Recall](#cascade-recall) — 3 route(s)
- [Prefetch & Metrics](#prefetch-metrics) — 1 route(s)
- [Tags & Domains](#tags-domains) — 5 route(s)
- [Profile Score](#profile-score) — 3 route(s)
- [Feedback & Sync](#feedback-sync) — 5 route(s)
- [Frozen Tier](#frozen-tier) — 2 route(s)
- [Dream Loop & Review Queue](#dream-loop-review-queue) — 5 route(s)
- [Other](#other) — 1 route(s)
- [Knowledge Graph — Triples](#knowledge-graph-triples) — 7 route(s)
- [Knowledge Graph — Communities](#knowledge-graph-communities) — 6 route(s)
- [Knowledge Graph — Entities](#knowledge-graph-entities) — 10 route(s)
- [Knowledge Graph — Maintenance](#knowledge-graph-maintenance) — 4 route(s)
- [Knowledge Graph — Intelligence](#knowledge-graph-intelligence) — 4 route(s)

## Memory Blocks

### `GET /api/memvault/blocks`

- **Scope:** `memvault.read`
- **Handler:** `list_blocks` ([routes.py:66](../apps/api/src/memvault/routes.py))

### `POST /api/memvault/blocks`

- **Scope:** `memvault.write`
- **Handler:** `create_block` ([routes.py:127](../apps/api/src/memvault/routes.py))

**Example request:**

```http
POST /api/memvault/blocks
Content-Type: application/json

{"content": "Postgres pgvector beats SQLite for hybrid search.", "block_type": "knowledge", "tags": ["postgres", "pgvector"]}
```

**Example response:**

```json
{"id": "01J...", "content": "Postgres pgvector beats SQLite for hybrid search.", "block_type": "knowledge", "tags": ["postgres","pgvector"], "confidence": 0.0, "created_at": "2026-04-28T08:00:00Z"}
```

### `DELETE /api/memvault/blocks/{block_id}`

- **Scope:** `memvault.write`
- **Handler:** `delete_block` ([routes.py:263](../apps/api/src/memvault/routes.py))
- **Description:** Aggregate blocks by source_session — block_count, first/last timestamps, block types.

### `GET /api/memvault/blocks/{block_id}`

- **Scope:** `memvault.read`
- **Handler:** `get_block` ([routes.py:114](../apps/api/src/memvault/routes.py))

### `PUT /api/memvault/blocks/{block_id}`

- **Scope:** `memvault.write`
- **Handler:** `update_block` ([routes.py:248](../apps/api/src/memvault/routes.py))

## Sessions

### `GET /api/memvault/sessions`

- **Scope:** `memvault.read`
- **Handler:** `list_sessions` ([routes.py:278](../apps/api/src/memvault/routes.py))
- **Description:** Aggregate blocks by source_session — block_count, first/last timestamps, block types.

## Search & Query

### `POST /api/memvault/inject`

- **Scope:** `memvault.read`
- **Handler:** `inject_memory` ([routes.py:551](../apps/api/src/memvault/routes.py))

**Example request:**

```http
POST /api/memvault/inject
Content-Type: application/json

{"q": "session warmup for postgres planning", "task_mode": "build", "load_budget": "deep"}
```

**Example response:**

```json
{"system_prompt_memory": "## Relevant memories\n- ...", "working_context": ["..."], "decision_bias": ["..."], "cards": [...]}
```

### `POST /api/memvault/inspect`

- **Scope:** `memvault.read`
- **Handler:** `inspect_memory` ([routes.py:562](../apps/api/src/memvault/routes.py))

### `POST /api/memvault/query`

- **Scope:** `memvault.read`
- **Handler:** `query_memory` ([routes.py:541](../apps/api/src/memvault/routes.py))

**Example request:**

```http
POST /api/memvault/query
Content-Type: application/json

{"q": "what did we decide about embeddings?", "task_mode": "lookup", "thinking_mode": "auto", "load_budget": "standard", "top_k": 6}
```

**Example response:**

```json
{"query": "...", "strategy": {...}, "cards": [{"id":"01J...","title":"...","summary":"..."}], "cascade_cards": [], "highlights": []}
```

### `GET /api/memvault/search`

- **Scope:** `memvault.read`
- **Handler:** `search` ([routes.py:340](../apps/api/src/memvault/routes.py))

**Example request:**

```http
GET /api/memvault/search?q=pgvector&top_k=5
```

**Example response:**

```json
{"results": [{"block": {...}, "score": 0.83}], "metadata": {"vector_used": true, "scoring_applied": true}}
```

## Cascade Recall

### `GET /api/memvault/kg/recall`

- **Scope:** `—`
- **Handler:** `cascade_recall` ([kg_routes.py:298](../apps/api/src/memvault/kg_routes.py))

**Example request:**

```http
GET /api/memvault/kg/recall?seed=memvault-os&depth=2&limit=20
```

**Example response:**

```json
{"triples": [...], "entities": [...], "scores": {...}}
```

### `GET /api/memvault/kg/session-context`

- **Scope:** `—`
- **Handler:** `get_session_context` ([kg_routes.py:526](../apps/api/src/memvault/kg_routes.py))

### `POST /api/memvault/recall/text`

- **Scope:** `—`
- **Handler:** `recall_text` ([routes.py:583](../apps/api/src/memvault/routes.py))

## Prefetch & Metrics

### `GET /api/memvault/prefetch/metrics`

- **Scope:** `memvault.read`
- **Handler:** `prefetch_metrics` ([routes.py:600](../apps/api/src/memvault/routes.py))

## Tags & Domains

### `GET /api/memvault/domains`

- **Scope:** `memvault.read`
- **Handler:** `list_domains` ([routes.py:656](../apps/api/src/memvault/routes.py))

### `POST /api/memvault/domains`

- **Scope:** `memvault.write`
- **Handler:** `create_domain` ([routes.py:668](../apps/api/src/memvault/routes.py))

### `PATCH /api/memvault/domains/{domain_id}`

- **Scope:** `memvault.write`
- **Handler:** `update_domain` ([routes.py:680](../apps/api/src/memvault/routes.py))

### `GET /api/memvault/tags`

- **Scope:** `memvault.read`
- **Handler:** `list_tags` ([routes.py:633](../apps/api/src/memvault/routes.py))

### `POST /api/memvault/tags/sync`

- **Scope:** `—`
- **Handler:** `sync_tags` ([routes.py:642](../apps/api/src/memvault/routes.py))

## Profile Score

### `GET /api/memvault/profile`

- **Scope:** `memvault.read`
- **Handler:** `get_profile` ([routes.py:697](../apps/api/src/memvault/routes.py))

### `PUT /api/memvault/profile`

- **Scope:** `memvault.write`
- **Handler:** `upsert_profile` ([routes.py:718](../apps/api/src/memvault/routes.py))
- **Description:** Recalculate profile scores from actual KG data (post-KAS separation).

### `POST /api/memvault/profile/recalculate`

- **Scope:** `memvault.write`
- **Handler:** `recalculate_profile` ([routes.py:730](../apps/api/src/memvault/routes.py))
- **Description:** Recalculate profile scores from actual KG data (post-KAS separation).

## Feedback & Sync

### `POST /api/memvault/feedback`

- **Scope:** `memvault.write`
- **Handler:** `record_feedback` ([routes.py:856](../apps/api/src/memvault/routes.py))
- **Description:** Record explicit relevance feedback for a search result (positive/negative).

### `GET /api/memvault/feedback/{entity_id}`

- **Scope:** `memvault.read`
- **Handler:** `get_feedback_aggregate` ([routes.py:887](../apps/api/src/memvault/routes.py))
- **Description:** Get aggregated feedback for a specific block.

### `GET /api/memvault/status`

- **Scope:** `—`
- **Handler:** `memvault_status` ([routes.py:903](../apps/api/src/memvault/routes.py))
- **Description:** List frozen block metadata (no content -- needs thaw).

### `POST /api/memvault/sync/scan`

- **Scope:** `—`
- **Handler:** `sync_scan` ([routes.py:837](../apps/api/src/memvault/routes.py))

### `GET /api/memvault/sync/stats`

- **Scope:** `—`
- **Handler:** `sync_stats` ([routes.py:792](../apps/api/src/memvault/routes.py))

## Frozen Tier

### `GET /api/memvault/frozen`

- **Scope:** `—`
- **Handler:** `list_frozen_blocks` ([routes.py:911](../apps/api/src/memvault/routes.py))
- **Description:** List frozen block metadata (no content -- needs thaw).

### `GET /api/memvault/frozen/{block_id}/thaw`

- **Scope:** `—`
- **Handler:** `thaw_frozen_block` ([routes.py:959](../apps/api/src/memvault/routes.py))

## Dream Loop & Review Queue

### `POST /api/memvault/dream`

- **Scope:** `memvault.write`
- **Handler:** `run_dream_consolidation` ([routes.py:1015](../apps/api/src/memvault/routes.py))

**Example request:**

```http
POST /api/memvault/dream
Content-Type: application/json

{"window_hours": 24, "max_blocks": 200}
```

**Example response:**

```json
{"consolidated": 12, "invalidated": 3, "review_queued": 2}
```

### `GET /api/memvault/review-queue`

- **Scope:** `memvault.read`
- **Handler:** `list_review_queue` ([routes.py:1040](../apps/api/src/memvault/routes.py))
- **Description:** List pending review items: __pending__ blocks + recent dream invalidations.

**Example request:**

```http
GET /api/memvault/review-queue?status=pending&limit=20
```

**Example response:**

```json
{"items": [{"id":"01J...","kind":"dream_invalidation","block_id":"...","reason":"..."}], "total": 1}
```

### `POST /api/memvault/review-queue/{item_id}/approve`

- **Scope:** `memvault.write`
- **Handler:** `approve_review` ([routes.py:1097](../apps/api/src/memvault/routes.py))
- **Description:** Approve a pending review item — confirms the dream/dedup decision.

### `POST /api/memvault/review-queue/{item_id}/defer`

- **Scope:** `memvault.write`
- **Handler:** `defer_review` ([routes.py:1155](../apps/api/src/memvault/routes.py))
- **Description:** Defer a review — mark as seen but keep pending.

### `POST /api/memvault/review-queue/{item_id}/reject`

- **Scope:** `memvault.write`
- **Handler:** `reject_review` ([routes.py:1126](../apps/api/src/memvault/routes.py))
- **Description:** Reject a pending review item — restores the block to active state.

## Other

### `GET /api/memvault/frontier/top`

- **Scope:** `memvault.read`
- **Handler:** `frontier_top` ([routes.py:1183](../apps/api/src/memvault/routes.py))

## Knowledge Graph — Triples

### `GET /api/memvault/kg/triples`

- **Scope:** `memvault.read`
- **Handler:** `list_triples` ([kg_routes.py:93](../apps/api/src/memvault/kg_routes.py))

### `POST /api/memvault/kg/triples`

- **Scope:** `memvault.write`
- **Handler:** `create_triple` ([kg_routes.py:62](../apps/api/src/memvault/kg_routes.py))

**Example request:**

```http
POST /api/memvault/kg/triples
Content-Type: application/json

{"subject": "memvault-os", "predicate": "uses", "object": "qdrant", "confidence": 0.9}
```

**Example response:**

```json
{"id": "01J...", "subject": "memvault-os", "predicate": "uses", "object": "qdrant", "confidence": 0.9}
```

### `POST /api/memvault/kg/triples/batch`

- **Scope:** `memvault.write`
- **Handler:** `batch_ingest_triples` ([kg_routes.py:81](../apps/api/src/memvault/kg_routes.py))

### `GET /api/memvault/kg/triples/search`

- **Scope:** `memvault.read`
- **Handler:** `search_triples` ([kg_routes.py:122](../apps/api/src/memvault/kg_routes.py))

### `DELETE /api/memvault/kg/triples/{triple_id}`

- **Scope:** `memvault.write`
- **Handler:** `delete_triple` ([kg_routes.py:137](../apps/api/src/memvault/kg_routes.py))

### `PUT /api/memvault/kg/triples/{triple_id}`

- **Scope:** `memvault.write`
- **Handler:** `update_triple` ([kg_routes.py:148](../apps/api/src/memvault/kg_routes.py))
- **Description:** Mark a triple as invalid (soft temporal invalidation).

### `PUT /api/memvault/kg/triples/{triple_id}/invalidate`

- **Scope:** `memvault.write`
- **Handler:** `invalidate_triple` ([kg_routes.py:161](../apps/api/src/memvault/kg_routes.py))
- **Description:** Mark a triple as invalid (soft temporal invalidation).

## Knowledge Graph — Communities

### `GET /api/memvault/kg/communities`

- **Scope:** `memvault.read`
- **Handler:** `list_communities` ([kg_routes.py:183](../apps/api/src/memvault/kg_routes.py))

### `POST /api/memvault/kg/communities/regenerate`

- **Scope:** `memvault.write`
- **Handler:** `regenerate_communities` ([kg_routes.py:205](../apps/api/src/memvault/kg_routes.py))

### `GET /api/memvault/kg/communities/{community_id}`

- **Scope:** `memvault.read`
- **Handler:** `get_community` ([kg_routes.py:193](../apps/api/src/memvault/kg_routes.py))

### `POST /api/memvault/kg/communities/{community_id}/description`

- **Scope:** `memvault.write`
- **Handler:** `update_community_description` ([kg_routes.py:247](../apps/api/src/memvault/kg_routes.py))
- **Description:** Update a community's description_zh field.

### `GET /api/memvault/kg/summaries`

- **Scope:** `memvault.read`
- **Handler:** `list_summaries` ([kg_routes.py:271](../apps/api/src/memvault/kg_routes.py))
- **Description:** Accept community summary data from community_summary_pipeline.py and save atomically.

### `POST /api/memvault/kg/summaries/regenerate`

- **Scope:** `—`
- **Handler:** `regenerate_summaries` ([kg_routes.py:283](../apps/api/src/memvault/kg_routes.py))
- **Description:** Accept community summary data from community_summary_pipeline.py and save atomically.

## Knowledge Graph — Entities

### `GET /api/memvault/kg/entities`

- **Scope:** `—`
- **Handler:** `list_entities` ([kg_routes.py:323](../apps/api/src/memvault/kg_routes.py))
- **Description:** List canonical entities with optional type filter.

### `POST /api/memvault/kg/entities/auto-merge`

- **Scope:** `—`
- **Handler:** `auto_merge_entities` ([kg_routes.py:425](../apps/api/src/memvault/kg_routes.py))
- **Description:** Auto-merge entity pairs above similarity threshold.

### `POST /api/memvault/kg/entities/backfill`

- **Scope:** `—`
- **Handler:** `backfill_entity_resolution` ([kg_routes.py:404](../apps/api/src/memvault/kg_routes.py))
- **Description:** Backfill entity resolution for triples missing canonical IDs.

### `POST /api/memvault/kg/entities/merge`

- **Scope:** `—`
- **Handler:** `merge_entities` ([kg_routes.py:389](../apps/api/src/memvault/kg_routes.py))
- **Description:** Merge secondary entity into primary.

### `GET /api/memvault/kg/entities/merge-candidates`

- **Scope:** `—`
- **Handler:** `entity_merge_candidates` ([kg_routes.py:366](../apps/api/src/memvault/kg_routes.py))
- **Description:** Find entities that are candidates for merging (high embedding similarity).

### `GET /api/memvault/kg/entities/stats`

- **Scope:** `—`
- **Handler:** `entity_stats` ([kg_routes.py:357](../apps/api/src/memvault/kg_routes.py))
- **Description:** Get entity resolution statistics.

### `GET /api/memvault/kg/entity-edges`

- **Scope:** `memvault.read`
- **Handler:** `list_entity_edges` ([kg_routes.py:831](../apps/api/src/memvault/kg_routes.py))
- **Description:** List entity edges with composite_weight >= min_weight.

### `POST /api/memvault/kg/entity-edges/recompute`

- **Scope:** `memvault.write`
- **Handler:** `recompute_edge_weights` ([kg_routes.py:887](../apps/api/src/memvault/kg_routes.py))
- **Description:** Trigger full edge weight recomputation via the Edge Pipeline.

### `GET /api/memvault/kg/entity-edges/surprises`

- **Scope:** `memvault.read`
- **Handler:** `get_surprise_connections` ([kg_routes.py:921](../apps/api/src/memvault/kg_routes.py))
- **Description:** Discover unexpected knowledge connections via multi-signal analysis.

### `GET /api/memvault/kg/traverse`

- **Scope:** `—`
- **Handler:** `graph_traverse` ([kg_routes.py:453](../apps/api/src/memvault/kg_routes.py))
- **Description:** Multi-hop graph traversal from a seed entity using recursive CTE.

## Knowledge Graph — Maintenance

### `POST /api/memvault/kg/embeddings/backfill`

- **Scope:** `—`
- **Handler:** `backfill_embeddings` ([kg_routes.py:482](../apps/api/src/memvault/kg_routes.py))

### `POST /api/memvault/kg/lint`

- **Scope:** `memvault.read`
- **Handler:** `lint_knowledge_graph` ([kg_routes.py:710](../apps/api/src/memvault/kg_routes.py))
- **Description:** Knowledge graph health check — detect contradictions, stale triples, orphans, etc.

### `GET /api/memvault/kg/lint/report`

- **Scope:** `memvault.read`
- **Handler:** `lint_health_report` ([kg_routes.py:775](../apps/api/src/memvault/kg_routes.py))
- **Description:** Run the 10 wiki-lint inspired health checks (report only — no remediation).

### `GET /api/memvault/kg/lint/report.md`

- **Scope:** `memvault.read`
- **Handler:** `lint_health_report_markdown` ([kg_routes.py:815](../apps/api/src/memvault/kg_routes.py))
- **Description:** Same as `/kg/lint/report` but returns wiki-lint markdown for humans/CLI.

## Knowledge Graph — Intelligence

### `POST /api/memvault/kg/intelligence/ingest`

- **Scope:** `—`
- **Handler:** `ingest_intelligence_digest` ([kg_routes.py:628](../apps/api/src/memvault/kg_routes.py))

### `GET /api/memvault/kg/interest/attention`

- **Scope:** `—`
- **Handler:** `get_attention_profile` ([kg_routes.py:682](../apps/api/src/memvault/kg_routes.py))

### `GET /api/memvault/kg/interest/gaps`

- **Scope:** `—`
- **Handler:** `get_knowledge_gaps` ([kg_routes.py:694](../apps/api/src/memvault/kg_routes.py))
- **Description:** Knowledge graph health check — detect contradictions, stale triples, orphans, etc.

### `POST /api/memvault/kg/interest/generate`

- **Scope:** `—`
- **Handler:** `generate_interest_snapshot` ([kg_routes.py:668](../apps/api/src/memvault/kg_routes.py))

---

## Regenerate

```bash
python3 scripts/build-api-docs.py
```

`docs/route_manifest.yaml` itself is regenerated by the upstream `scripts/build_manifest.py` whenever `apps/api/src/memvault/routes.py` or `kg_routes.py` change.
