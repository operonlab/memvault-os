# memvault-os

<p align="center">
  <strong><a href="README.md">English</a></strong> | <a href="README.zh.md">繁體中文</a>
</p>

<p align="center">
  <a href="https://github.com/operonlab/memvault-os/actions/workflows/lint.yml"><img alt="Lint" src="https://img.shields.io/github/actions/workflow/status/operonlab/memvault-os/lint.yml?branch=main&label=lint&style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/actions/workflows/test.yml"><img alt="Tests" src="https://img.shields.io/github/actions/workflow/status/operonlab/memvault-os/test.yml?branch=main&label=tests&style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/actions/workflows/build-images.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/operonlab/memvault-os/build-images.yml?branch=main&label=build&style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/releases"><img alt="Release" src="https://img.shields.io/github/v/release/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://www.python.org/"><img alt="Python" src="https://img.shields.io/badge/python-3.12+-blue?style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://github.com/operonlab/memvault-os/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/operonlab/memvault-os?style=flat-square"></a>
  <a href="https://deepwiki.com/operonlab/memvault-os"><img alt="DeepWiki" src="https://img.shields.io/badge/DeepWiki-explore-blue?style=flat-square"></a>
</p>

> **Self-hosted long-term memory for LLM agents** — knowledge graph + semantic search + dream-loop reflection, packaged for one-command Docker install on macOS / Linux / Windows.

## 🚧 Status: v0.1 — Active Development (Pre-Release)

Recently extracted from the [Workshop modular monolith](https://github.com/JonesHong/workshop). **No public release yet.**

| Item | Status |
|------|--------|
| Docker stack boots | ✅ All 6 services healthy (postgres / redis / qdrant / litellm / embed-gateway / api) |
| Alembic baseline migration | ✅ 17 tables in one migration |
| Real E2E HTTP tests | ✅ 39 / 41 pass (95.1%) |
| Pre-built images on ghcr.io | ❌ Not yet — users must `docker compose build` from source |
| ONNX CPU embedding model artifact | ❌ Download script TBD; current fallback returns zero vectors |
| `install.sh` placeholder-digest guard | ❌ One-command install will abort at `docker compose pull` |
| Web frontend production build | ⚠️ TypeScript error (`actionJournal.ts:124` — fix pending) |
| Linux / Windows verified | ❌ Currently tested only on macOS Apple Silicon |

The "one-click install" path described below works once the v1.0.0 release ships images to ghcr.io. **For now, follow the [build-from-source](#run-from-source-current-only-supported-path) section.**

---

## Features (implemented)

- **66 REST endpoints** — memory blocks CRUD, hybrid search, KG triples, communities, recall, dream loop, slow-thinker
- **Hybrid search** — Qdrant dense + BM25 fusion, plus Postgres tsvector full-text and CJK ILIKE
- **Knowledge graph** — auto-evolving triples, entity resolution, community summaries, PPR retrieval
- **Cross-platform embeddings (3-tier auto-detect)** — MLX on Apple Silicon, vLLM on NVIDIA GPU, ONNX Runtime fallback elsewhere
- **Multi-LLM** — bundled LiteLLM proxy, plug any OpenAI / Anthropic / Gemini / DeepSeek key
- **Single-user mode V1** — no auth ceremony; double-click and use

---

## Run from Source (current, only supported path)

### Prerequisites

- Docker Desktop 24.0+
- macOS Apple Silicon (other platforms not yet verified)
- ≥ 5 GB disk, ≥ 4 GB RAM
- One LLM provider API key (OpenAI / Anthropic / Gemini / DeepSeek)

### Steps

```bash
# 1. Clone
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os

# 2. Generate secrets
bash scripts/generate-secrets.sh

# 3. Pin third-party image digests
bash scripts/pin-images.sh

# 4. Optional: change host ports if 8080 / 3000 are taken
echo "API_PORT=18080" >> .env
echo "WEB_PORT=13000" >> .env

# 5. Build local images via dev override
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env build

# 6. Bring up storage layer first
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d postgres redis qdrant

# 7. Run baseline migration (17 tables)
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env run --rm --no-deps api alembic upgrade head

# 8. Bring up the rest of the stack
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d

# 9. Verify
curl http://localhost:18080/health/readiness
# {"status":"ok","checks":{"database":"ok","redis":"ok","qdrant":"ok"}}
```

### Run the E2E test suite

```bash
cd apps/api
uv venv .e2e-venv --python 3.12
uv pip install --python .e2e-venv/bin/python pytest pytest-asyncio httpx
MEMVAULT_TEST_BASE_URL=http://localhost:18080 \
  ./.e2e-venv/bin/python -m pytest tests/test_e2e_api.py -v
```

Expected: 39 pass / 2 fail. The 2 failing tests are test-contract drift (test posts wrong shape; not API bugs) and tracked in [issues](https://github.com/operonlab/memvault-os/issues).

---

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌─────────────┐
│ memvault-web │  │ memvault-api │  │   worker    │
│   (Nginx)    │←→│  (FastAPI)   │←→│ (cron jobs) │
│   :13000     │  │    :18080    │  │  internal   │
└──────────────┘  └──────────────┘  └─────────────┘
                          ↓
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│ postgres │  │  redis   │  │  qdrant  │  │ litellm  │  │ embed-gateway│
│ pgvector │  │ 7-alpine │  │ v1.12.4  │  │ proxy    │  │ MLX/vLLM/ONNX│
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘
```

- Only `web` (3000) and `api` (8080) bind to host. Everything else is on the internal `memvault-net` network.
- The `embed-gateway` container routes embedding requests to MLX (host sidecar via `host.docker.internal`), vLLM (sidecar container), or its built-in ONNX Runtime backend, based on `EMBED_BACKEND`.

---

## Roadmap to v1.0.0

In priority order (highest first):

1. **GitHub Actions auto-build & push to ghcr.io** so `install.sh` can really pull. *Current biggest blocker.*
2. **ONNX model download step** — `scripts/download-models.sh` or first-run hook to fetch Qwen3-Embedding-0.6B (~600 MB).
3. **`install.sh` placeholder-digest guard** — detect `sha256:000…` and downgrade to build mode instead of aborting.
4. **Codex code-review follow-ups** —
   - `kg_services.batch_ingest` IntegrityError rollback over-rollbacks committed rows.
   - ONNX backend should fail-closed (`/health` returns 503) when the model file is missing, instead of silently emitting zero vectors.
   - `audit_stub.ENABLED` honored — fixed in v3.2 ✅
5. **Web build TypeScript fix** — `actionJournal.ts:124` Window cast.
6. **Linux + Windows verified end-to-end** — three-tier `install.sh` paths.

---

## Origin

The full design plan lives in [`docs/plan-v3.2.md`](./docs/plan-v3.2.md) (684 lines), distilled through five rounds of codex review.

Key decisions:
- **Docker Compose, not Tauri** — preserves Postgres pgvector / tsvector / GIN / partial-unique indexes verbatim.
- **Single-user mode V1** — no multi-tenant complexity in the first OSS cut.
- **Fresh baseline migration** — does not carry the monorepo's 25+ historical alembic chain.
- **Auth stub + audit stub** — `require_permission()` and `_record_audit()` have local stubs replacing the monorepo's admin module.

---

## License

MIT — see [LICENSE](./LICENSE).
