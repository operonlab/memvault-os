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

## ✅ Status: v1.0.1 — install hardening

v1.0.0 shipped a working stack but the fresh-clone install had several
silent foot-guns. v1.0.1 closes them — the goal is **a non-technical
macOS user can `git clone` and run one command**.

| Item | Status |
|------|--------|
| Fresh-clone `install.sh` end-to-end (macOS Apple Silicon, real PTY) | ✅ verified — 8 / 8 containers up, alembic 18 tables, web UI HTTP 200 |
| Real E2E HTTP tests | ✅ **42 / 42** pass (was 40 / 42 in v1.0.0) |
| Pre-built images on ghcr.io | ✅ Public — `ghcr.io/operonlab/memvault-{api,web,embed-gateway}:1.0.0` |
| Install regression tests in CI | ✅ 11 static checks guard the install path against regressions |
| Codex adversarial review (v1.0.0) | ✅ All 6 findings (2 critical / 3 high / 1 medium) addressed |
| Offline / no-key install mode | ✅ Stack boots without any LLM key — fill `.env` later, then `doctor.sh` |
| Linux verified end-to-end | ⚠️ Pre-built images known to work; install scripts only smoke-tested on macOS |
| Windows install.ps1 verified | ⚠️ Same scaffold as macOS but not yet run end-to-end |

### What changed since v1.0.0

12 fresh-clone install blockers fixed. Each has a static regression test:

- **A** preflight prompted for a port replacement, then silently dropped the new port (`.env` did not exist yet).
- **B** secrets were generated as base64 and ended up containing `+` / `/` / `=` which broke `postgresql://` and `redis://` URL parsing.
- **C** the LLM smoke test container was started **before** `pin-images.sh` ran, so a fresh `LITELLM_DIGEST=sha256:000…` placeholder caused `manifest unknown`.
- **D** `configure_llm` had no skip path — users without any LLM key got stuck in an infinite re-prompt.
- **E** `pin-images.sh` resolved `litellm:v1.55.10` (which never existed on ghcr) instead of `main-stable` (what compose actually uses).
- **F / G** `worker` and `wait_for_healthy` blocked on `litellm` being healthy. `litellm`'s prisma layer in `main-stable` is currently flaky and may stay at `health: starting`. Both now treat `litellm` as best-effort.
- **H** `alembic upgrade head` exited 0 but created **zero** tables: `CREATE SCHEMA` was issued before `context.begin_transaction()`, the auto-began tx was never committed by alembic, and the async connection rolled it back on close.
- **J** the LLM provider menu printed to stdout, but the caller captured stdout with `$()` — the chosen number became part of the menu text and never matched the case branch.
- **K** `read -r` does not strip carriage returns; PTY drivers and Windows CRLF stdin left `\r` behind, breaking case- and regex-matches.
- **L** the `worker` service reused the api image but `apps/worker/` was never `COPY`'d into it (`build.context: ../apps/api` could not see `apps/worker`).

---

## Quick start (macOS / Linux)

### Prerequisites

- Docker Desktop 24.0+
- macOS Apple Silicon recommended (Linux x86_64 should work; only macOS is fully verified end-to-end)
- ≥ 5 GB disk, ≥ 4 GB RAM
- **No LLM key required to install** — pick offline mode and add a key later

### One-command install

```bash
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os
bash scripts/install.sh
```

The installer is interactive but every prompt has a sensible safe default:

1. **Pre-flight** — checks Docker, RAM, disk, host ports. If `8080` (api) or `3000` (web) is already in use it asks for an alternate port and writes it back into `.env`.
2. **Secrets** — `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `MEMVAULT_SECRET_KEY` / `LITELLM_MASTER_KEY` generated as URL-safe hex.
3. **Embedding backend (auto)** — Apple Silicon → MLX sidecar; NVIDIA GPU → vLLM container; otherwise → ONNX Runtime CPU.
4. **Image preparation** — pins third-party digests then builds api / web / embed-gateway from source.
5. **LLM provider (interactive)** — pick `1) OpenAI`, `2) Anthropic`, `3) Gemini`, `4) DeepSeek`, `5) local Ollama`, or **`6) skip for now (offline mode)`**. Picking 6 still gives a fully running stack; LLM-dependent endpoints (briefing / synth / triple-extract) just return `503` until you add a key.
6. **Compose up** — pulls third-party images, brings up the stack, polls the required services for `healthy` (postgres / redis / qdrant / embed-gateway / api). litellm is best-effort.
7. **Alembic** — applies the 17-table baseline migration, prints `memvault schema 共 18 張表`.
8. **Done** — opens `scripts/post-install.html`. Web UI: <http://localhost:3000>, API: <http://localhost:8080> (or whatever ports you picked).

### Add an LLM key later

```bash
# Edit .env and set ONE of these:
echo "OPENAI_API_KEY=sk-..." >> .env
# or ANTHROPIC_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY

docker compose restart litellm
bash scripts/doctor.sh   # walks every service and reports green/red
```

### Non-interactive (CI / scripted)

```bash
WEB_PORT=23000 API_PORT=28080 MEMVAULT_SKIP_LLM=1 bash scripts/install.sh
```

`MEMVAULT_SKIP_LLM=1` (or `OFFLINE_MODE=1`) bypasses the interactive provider menu and writes `MEMVAULT_LLM_DEFERRED=1` to `.env` so `doctor.sh` knows to give a friendly recovery hint instead of a hard failure.

### Run the E2E test suite

```bash
cd apps/api
uv venv .e2e-venv --python 3.12
uv pip install --python .e2e-venv/bin/python pytest pytest-asyncio httpx
MEMVAULT_TEST_BASE_URL=http://localhost:8080 \
  ./.e2e-venv/bin/python -m pytest tests/test_e2e_api.py -v
```

Expected: **42 / 42 pass**.

---

## Architecture

```
┌──────────────┐  ┌──────────────┐  ┌─────────────┐
│ memvault-web │  │ memvault-api │  │   worker    │
│   (Nginx)    │←→│  (FastAPI)   │←→│ (cron jobs) │
│   :3000      │  │    :8080     │  │  internal   │
└──────────────┘  └──────────────┘  └─────────────┘
                          ↓
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
│ postgres │  │  redis   │  │  qdrant  │  │ litellm  │  │ embed-gateway│
│ pgvector │  │ 7-alpine │  │ v1.12.4  │  │ proxy    │  │ MLX/vLLM/ONNX│
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────────┘
```

- Only `web` (3000) and `api` (8080) bind to host. Everything else lives on the internal `memvault-net` network.
- The `embed-gateway` container routes embedding requests to MLX (host sidecar via `host.docker.internal`), vLLM (sidecar container), or its built-in ONNX Runtime backend, based on `EMBED_BACKEND`.
- The `worker` container reuses the api image with a different CMD (`python -m apps.worker.main`) — same Python source, same dependencies, no second image to maintain.

---

## Features (implemented)

- **66 REST endpoints** — memory blocks CRUD, hybrid search, KG triples, communities, recall, dream loop, slow-thinker
- **Hybrid search** — Qdrant dense + BM25 fusion, plus Postgres tsvector full-text and CJK ILIKE
- **Knowledge graph** — auto-evolving triples, entity resolution, community summaries, PPR retrieval
- **Cross-platform embeddings (3-tier auto-detect)** — MLX on Apple Silicon, vLLM on NVIDIA GPU, ONNX Runtime fallback elsewhere
- **Multi-LLM** — bundled LiteLLM proxy, plug any OpenAI / Anthropic / Gemini / DeepSeek key
- **Single-user mode V1** — no auth ceremony; double-click and use

---

## Roadmap (post v1.0.1)

- **Linux + Windows verified end-to-end** — three-tier `install.sh` / `install.ps1` paths.
- **`scripts/configure-llm.sh`** — guided LLM key entry + smoke test for users who installed in offline mode.
- **`curl … | bash` install path** — clone-from-curl currently exists in the script but has not been end-to-end tested.
- **Idempotent re-install** — second run on an existing install should converge cleanly without volume conflicts.

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
