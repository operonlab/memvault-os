# Changelog

All notable changes to memvault-os.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v1.0.0 (2026-04-28)

First public release of memvault-os, distilled from the Workshop monorepo's
`core/src/modules/memvault/` + `workbench/src/modules/memvault/` modules into a
self-hosted, Docker-Compose-deployable application.

### Added — Application surface

- **66 REST endpoints** spanning memory blocks (CRUD), hybrid search,
  knowledge-graph triples / communities / entities, recall + injection,
  prefetch metrics, dream consolidation loop, review queue, frozen tier,
  feedback aggregation. See [`docs/api-reference.md`](./docs/api-reference.md).
- **17-table fresh baseline migration** under `apps/api/alembic/versions/`,
  collapsing the 25+ historical Alembic chain from the monorepo into a single
  `m0n0p0q0r0_baseline` revision (memvault schema + audit_logs mirror).
- **Hybrid search pipeline** — Qdrant dense vectors fused with BM25, plus
  Postgres `tsvector` full-text and CJK `ILIKE` fallback.
- **Knowledge graph** — auto-evolving triples with entity resolution,
  community detection + summaries, edge weight pipeline, PPR-based cascade
  recall, surprise connection discovery, multi-hop traversal via recursive
  CTE.
- **Dream loop + review queue** — overnight consolidation produces
  invalidations and dedup proposals routed through an explicit
  approve / reject / defer queue.
- **Memory query orchestrator** (`/api/memvault/query`, `inject`, `inspect`)
  with task-mode / thinking-mode / load-budget / consumer dimensions and a
  cascade-recall second pass.

### Added — Embedding & LLM

- **Three-tier embedding auto-detect** — `embed-gateway` container routes to
  MLX (Mac sidecar via `host.docker.internal`), vLLM (sidecar container), or
  built-in ONNX Runtime, controlled by `EMBED_BACKEND`.
- **`infra/mlx-sidecar/`** — host-side LaunchAgent installer for Apple
  Silicon, exposing the MLX worker over an HTTP socket.
- **Bundled LiteLLM proxy** — single `LITELLM_API_KEY` in the api container,
  routes upstream to OpenAI / Anthropic / Gemini / DeepSeek / Ollama based on
  `.env`. The installer enforces at least one provider.

### Added — Compose stack

- `infra/docker-compose.yml` (base) + four overlays:
  - `docker-compose.dev.yml` — local-build mode (no ghcr pulls).
  - `docker-compose.frozen.yml` — pinned digests for reproducible installs.
  - `docker-compose.gpu.yml` — vLLM GPU sidecar.
  - `docker-compose.mac.yml` — MLX sidecar host networking glue.
- Internal-only `memvault-net` network; only `web` (3000) and `api` (8080)
  bind to the host.
- Per-service Dockerfiles under `apps/api/`, `apps/web/`, `apps/worker/`,
  `apps/embed-gateway/`.

### Added — Installers & lifecycle

- **`scripts/install.sh`** — one-command macOS / Linux installer
  (preflight → clone → secrets → embed-backend pick → LLM prompt →
  compose up → health poll → migrate → post-install page).
- **`scripts/install.ps1`** — Windows 11 + Docker Desktop + WSL2 equivalent
  with WSL CUDA detection.
- `scripts/generate-secrets.{sh,ps1}`, `pin-images.sh`, `preflight.sh`,
  `backup.sh`, `restore.sh`, `upgrade.sh`, `doctor.sh`, `uninstall.sh`,
  `_lib.sh` (shared shell helpers), `post-install.html`.

### Added — Web frontend

- `apps/web/` — extracted `workbench/src/modules/memvault/` as a standalone
  Vite + React 19 app served by Nginx, behind the api on the internal
  network.
- Inventory and gap analysis recorded in
  [`docs/web_dependency_inventory.md`](./docs/web_dependency_inventory.md).

### Added — Tests

- **39 / 41 real end-to-end HTTP tests** passing against a live
  docker-compose stack (`apps/api/tests/test_e2e_api.py`). The two failing
  tests are test-contract drift, not API bugs.
- Smoke tests for the api / worker / embed-gateway entry points
  (`apps/api/tests/test_smoke.py`).
- The Six-Iron-Rules of test-writing separation enforced (no production code
  modified to satisfy a test).

### Added — Documentation

- [`README.md`](./README.md) + [`README.zh.md`](./README.zh.md) — honest
  status banner, source-build path, roadmap to v1.0.0.
- [`docs/plan-v3.2.md`](./docs/plan-v3.2.md) — 684-line design plan after
  five rounds of codex review.
- [`docs/quickstart-linux.md`](./docs/quickstart-linux.md) — Linux + GPU
  setup with embed-backend troubleshooting.
- [`docs/quickstart-windows.md`](./docs/quickstart-windows.md) — Windows 11
  + WSL2 + Docker Desktop walkthrough.
- [`docs/api-reference.md`](./docs/api-reference.md) — auto-generated from
  `docs/route_manifest.yaml` by `scripts/build-api-docs.py`.
- [`docs/operations.md`](./docs/operations.md) — backup / restore / upgrade
  / doctor / uninstall runbook.
- [`docs/embedding_drift_patches.md`](./docs/embedding_drift_patches.md) —
  four runtime drift fixes applied during the Qdrant migration.
- [`CONTRIBUTING.md`](./CONTRIBUTING.md) — dev environment setup, lint,
  tests, PR flow.

### Added — CI

- `.github/workflows/lint.yml`, `test.yml`, `build-images.yml` — linting
  (ruff + biome), unit / smoke tests, and ghcr.io image builds gated on
  manifest pinning.

### Fixed

- Compose-level healthcheck pointed at the non-existent `/health` route,
  reporting `(unhealthy)` even though the api was responding correctly. The
  endpoint is `/health/liveliness`; the override is removed so the
  Dockerfile `HEALTHCHECK` wins (`ab92e11`).
- Audit-stub `ENABLED` flag now honored — when the embedded auth is in
  single-user mode the audit recorder no-ops instead of raising
  (`32dde16`).
- Real-stack E2E breakthrough — first pass got 24 / 41 green by fixing
  schema drift between test fixtures and the new baseline migration
  (`9133229`); the follow-up wave brought it to 39 / 41 by aligning request
  payloads with current Pydantic schemas (`32dde16`).

### Known gaps (tracked in roadmap)

- ghcr.io images not yet auto-published — `install.sh` falls back to source
  build when it sees a placeholder digest.
- ONNX model fetch script (`scripts/download-models.sh`) not yet present;
  the gateway currently fails closed when weights are missing.
- `apps/web` production build has one outstanding TypeScript error
  (`actionJournal.ts:124`).
- Linux + Windows three-tier installer paths verified in CI; full
  end-to-end manual smoke on bare metal still pending.

### Origin

Extracted from the [Workshop modular monolith](https://github.com/JonesHong/workshop)
across ten commits between `cd1f740` (Phase 0 — manifests + stubs) and
`ab92e11` (healthcheck fix). Phase boundaries:

| Phase | Commits | Outcome |
|-------|---------|---------|
| 0 — Scaffold | `cd1f740` | Manifests freezing the V1 surface |
| 1 — Repo split | `028ceec`, `d9f5c90` | 4-app layout + shared layer + smoke tests |
| 2 — Installers | `361e3e2`, `f939df5` | install.sh + install.ps1 + lifecycle scripts + CI |
| 3 — E2E hardening | `b064ef6`, `9133229`, `32dde16` | 0 → 39 / 41 passing tests |
| 4 — Polish | `c7fd515`, `ab92e11` | README rewrite + healthcheck fix |
