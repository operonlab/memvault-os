# memvault-os

<p align="center">
  <a href="README.md">English</a> | <strong><a href="README.zh.md">繁體中文</a></strong>
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

> **為 LLM agent 設計的自架長期記憶系統** — 知識圖譜 + 語意搜尋 + dream-loop 反思整合。在 macOS / Linux / Windows 一鍵 Docker 安裝。

## 🚧 目前狀態：v0.1 開發中

這個專案剛從 [Workshop modular monolith](https://github.com/JonesHong/workshop) 抽出，**尚未首次 release**。

| 項目 | 現況 |
|------|------|
| Docker stack 起得來 | ✅ 6 服務全部 healthy（postgres / redis / qdrant / litellm / embed-gateway / api） |
| Alembic baseline migration | ✅ 17 表一鍵建立 |
| 真實 E2E HTTP test | ✅ 39 / 41 通過（95.1%） |
| 自家 image 在 ghcr.io | ❌ 尚未發布 — 使用者目前需 `docker compose build` 自己 build |
| ONNX CPU embedding 模型 | ❌ 尚未提供下載步驟（fallback 仍會回零向量） |
| install.sh 的 placeholder digest 防呆 | ❌ 偵測未做，curl-pipe-bash 會在 pull 階段中止 |
| web 前端 production build | ⚠️ TypeScript error（`actionJournal.ts:124` 待修） |
| Linux / Windows 實機驗證 | ❌ 目前只在 macOS Apple Silicon 跑過 |

---

## 功能特色（已實作）

- **66 個 REST endpoint** — block CRUD、混合搜尋、KG triples、社群偵測、recall、dream loop、slow-thinker
- **混合搜尋** — Qdrant dense + BM25 fusion + Postgres tsvector 全文 + CJK ILIKE
- **知識圖譜** — 自動演化 triples、實體解析、社群摘要、PPR retrieval
- **跨平台 embedding（三軌偵測）** — Apple Silicon 走 MLX、有 NVIDIA GPU 走 vLLM、其他平台 ONNX Runtime
- **多 LLM provider** — 內建 LiteLLM proxy，OpenAI / Anthropic / Gemini / DeepSeek 任選一個 key 配置
- **單人模式（Single-user V1）** — 沒 auth 包袱，雙擊跑起來自己用

---

## 給開發者：用 source build 跑起來（目前唯一可行路線）

### 前置需求

- Docker Desktop 24.0+
- macOS Apple Silicon（其他平台尚未驗）
- 至少 5 GB 磁碟空間 / 4 GB RAM
- 至少一個 LLM provider 的 API key（OpenAI / Anthropic / Gemini / DeepSeek 其一）

### 步驟

```bash
# 1. clone
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os

# 2. 產生 secret
bash scripts/generate-secrets.sh

# 3. 把第三方 image digest pin 進 .env
bash scripts/pin-images.sh

# 4. 視情況改 host port（例如 host 8080 已被佔用）
echo "API_PORT=18080" >> .env
echo "WEB_PORT=13000" >> .env

# 5. 用 dev override build 自家 image
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env build

# 6. 起 storage layer
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d postgres redis qdrant

# 7. 跑 baseline migration（17 表）
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env run --rm --no-deps api alembic upgrade head

# 8. 起其他服務
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml \
  --env-file .env up -d

# 9. 驗證
curl http://localhost:18080/health/readiness
# {"status":"ok","checks":{"database":"ok","redis":"ok","qdrant":"ok"}}
```

### 跑 E2E 測試

```bash
cd apps/api
uv venv .e2e-venv --python 3.12
uv pip install --python .e2e-venv/bin/python pytest pytest-asyncio httpx
MEMVAULT_TEST_BASE_URL=http://localhost:18080 \
  ./.e2e-venv/bin/python -m pytest tests/test_e2e_api.py -v
```

預期：39 個 pass / 2 個 fail（後者是 test contract 與 API 真實簽名不符的 follow-up，不是 API bug）。

---

## 架構快覽

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

- **只有 web (3000) + api (8080) bind 到 host**，其他全 internal network
- **Embedding 三軌**：embed-gateway 內部依 `EMBED_BACKEND` 切 `mlx_proxy` / `vllm_proxy` / `onnx`

---

## Roadmap（朝向 v1.0.0）

優先序高 → 低：

1. **CI 自動 build push image 到 ghcr.io** — 讓 `install.sh` 真能 pull 跑（現在最大阻塞）
2. **ONNX 模型自動下載步驟** — `scripts/download-models.sh` 或 install 第一次跑時下載 Qwen3-Embedding-0.6B（~600 MB）
3. **install.sh placeholder digest 防呆** — 偵測到 sha256:000... 自動降級到 build mode 而非中止
4. **修 codex code review 找到的 high/medium bug**
   - `kg_services.batch_ingest` IntegrityError rollback over-rollback（會回滾已 commit 的）
   - `audit_stub.ENABLED` flag 已實作 ✅（v3.2 final 時補）
   - ONNX 找不到模型時的 fail-closed（讓 `/health` 回 503，不要靜默回零向量）
5. **web build TS error fix**（`actionJournal.ts:124` Window cast）
6. **Linux + Windows 實機驗證**（三軌 install script）

---

## 設計脈絡

完整 design plan 在 [`docs/plan-v3.2.md`](./docs/plan-v3.2.md)（684 行），經 5 輪 codex review 收斂。

關鍵決策：
- **走 Docker Compose 而非 Tauri** — 保留 Postgres pgvector / tsvector / GIN / partial unique index 全功能
- **Single-user mode V1** — 不做 multi-user，OSS 第一刀盡量小
- **Fresh baseline migration** — 不搬 monorepo 的 25+ 支歷史 migration
- **Auth stub + Audit stub** — `require_permission()` / `_record_audit()` 有 stub 替代 monorepo 的 admin 模組

---

## License

MIT — 詳見 [LICENSE](./LICENSE)。
