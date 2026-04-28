# Plan: memvault-os — Docker Compose 開源化 + 跨 OS 一鍵安裝

## Context

少爺想把 `core/src/modules/memvault/` 從 Workshop monolith 抽出，開源成獨立專案，讓沒有技術背景的新手也能在 macOS / Linux / Windows 一鍵裝起來、功能完整可用。

選擇 Docker Compose 路線（不是 Tauri）的理由：
- 保留 Postgres 全功能（pgvector / tsvector / GIN / JSONB / partial unique index），memvault 中英混合搜尋與 alias overlap 不退化
- 保留 Qdrant hybrid search（dense + BM25 fusion），規模可達百萬筆以上
- PPR / igraph 圖查詢照搬

## 現況盤點（Phase 1 Explore + Codex 二審驗證後）

### 耦合面

| 類型 | 數量 | 評估 |
|------|------|------|
| 跨模組 import (memvault → 外) | 1 個（`auth.permissions.has_permission` in `kg_routes.py:12`） | stub 即可 |
| **跨模組 import (shared → 外)** | **1 個（`src.shared.services.BaseCRUDService` → `src.modules.admin.models.AuditLog`）** | **不能只搬 memvault；shared 也要清 admin/auth 殘留** |
| 共用 utility 依賴 | qdrant_search ×13, embedding ×7, database ×7, reactive ×8, redis ×5, cache ×4, access_tracker ×4, rlm_engine ×3, text_utils ×5 | 抽 **最小公共層** `memvault_os/shared/`，**不照搬** Workshop `src/shared/` |
| **Alembic migration（修正）** | **本地實際 ~25 支 memvault 相關 migration / ~16 張表（總 56 支 migration 中 71 個檔涉及 memvault keywords）** | **不搬歷史鏈，重新 generate 一支 fresh baseline migration** |
| **Routes（驗證後修正）** | **66 個 route decorator**（`routes.py` + `kg_routes.py` + `grc_*` 子路由），需 freeze 一份 `route_manifest.yaml` 鎖定 V1 範圍 | 全保留 |
| **Python 檔案數（驗證後修正）** | **81 檔（非測試）** | memvault module 比 v1 計畫盤點大 |
| **Embedding ORM 與 runtime drift（codex 三審新發現）** | ORM 已無 `MemoryBlock.embedding` / `Triple.embedding` 欄位，但程式碼 4 處仍寫入/查詢：`kg_routes.py:74`、`kg_routes.py:500`、`kg_routes.py:513`、`services.py:995` | **採方案 (A) 全走 Qdrant**：清這 4 處 runtime bug，不重加 ORM 欄位 |
| **`src.events` / `src.config` 依賴（codex 三審新發現）** | 7+ 處 import `from src.events.bus`、`from src.events.types import MemvaultEvents`、`from src.config import settings` | shared 最小層要含 `events_stub.py` + `config_stub.py`，不只 utility |
| 事件流 | `events.py` wires 5 條 reactive flow（MEMORY_STORED / capture.promoted / digest.completed / query.completed / capture.entry.created） | 保留 in-process EventBus，外部模組事件改 stub |
| **/frozen/* 端點 storage 依賴** | `routes.py` 有 `/frozen` / `/frozen/{block_id}/thaw`，blocks 用 `s3://` archive 路徑 | **加 MinIO container 或 feature flag 關 frozen tier** |
| **背景任務** | dream / slow_thinker / sleeptime / interest snapshot / reindex / backfill 全在 API process | **拆 `memvault-worker` container**，不該塞 API |

### 既有可重用資產

- **`infra/docker/docker-compose.yml`** 已有 Postgres(pgvector) + Redis + Qdrant 基礎堆疊
- **`infra/docker/init.sql`** 已建 `memvault` schema
- **`stations/envkit/bootstrap/phase1-infra.sh`** 是 idempotent 安裝腳本範本
- **前端** `workbench/src/modules/memvault/`（galaxy 3D + browser + dashboard，30 個 .tsx）可以拆出當獨立 React app
- **`core/src/shared/qdrant_client.py` / `qdrant_search.py` / `embedding.py`** 抽象層已存在，只差環境變數化

### 需重做的東西（v2，吃下 codex 二審）

1. **MLX → 跨平台 embedding gateway**：`~/.venvs/omlx/embed_worker.py` Apple Silicon only。三軌（MLX / vLLM / CPU fallback）統一 HTTP 介面 `embed-gateway`。
2. **環境變數化**：`QDRANT_HOST`、`LITELLM_BASE`、`LITELLM_KEY` 全 `os.getenv()`。
3. **Auth stub 寫法修正**：原計畫 `async def require_permission()` 會被 FastAPI 當 coroutine default，**Depends 不會注入**。必須改 `Depends(...)` factory；`has_permission(role: str, scope: str)`（不是 user dict）。
4. **Event bus**：保留 in-process 版本，跨模組事件改 no-op stub 或外部 webhook 接點。
5. **Audit log 處理**：BaseCRUDService import `admin.AuditLog` → 帶最小 `audit_logs` 表，或加 `MEMVAULT_AUDIT_ENABLED=false` feature flag 關掉。
6. **Frozen tier 處理**：加 MinIO container（S3-compatible，pin version），或 `MEMVAULT_FROZEN_TIER=disabled` feature flag。
7. **Worker 拆分**：dream / slow_thinker / sleeptime / reindex / backfill → `memvault-worker` container，與 API 共享 codebase 但不同 entrypoint。
8. **CPU fallback 模型決策**：FastEmbed 官方不支援 Qwen3-Embedding-0.6B。兩條路：(a) 自寫 ONNX Runtime wrapper 跑 Qwen3 ONNX；(b) 換官方 1024d 模型如 `mxbai-embed-large-v1`，但中英效果需重測。**1024 維是硬約束**（Qdrant collection 寫死），不能改 384/768 維模型。
9. **Image pin version**：所有 image 改 `image@sha256:...` 或具體 tag（不用 `latest` / `main-stable` / `main`）。
10. **Compose network 收斂**：Postgres / Redis / Qdrant / MinIO 不 expose 到 host port，僅 internal network；只有 web (3000) + API (8080) 對外。
11. **LLM 強制可用路線**：installer 互動式至少要選一條（OpenAI / Anthropic / Gemini / DeepSeek key 任一，或本地 Ollama），避免「裝好但 KG/dream 不能用」。

---

## 設計：memvault-os 架構

### Repo 結構（建議獨立新 repo `operonlab/memvault-os`）

```
memvault-os/
├── apps/
│   ├── api/                  # FastAPI (memvault 模組 + 必要 shared 最小公共層)
│   │   ├── src/memvault/     # 從 core/src/modules/memvault/ 搬（81 檔非測試）
│   │   ├── src/shared/       # 自家最小層（不照搬 Workshop src/shared/）
│   │   ├── src/events_stub/  # bus.py + types.py（替代 src.events）
│   │   ├── src/config_stub.py # 替代 src.config.settings（env 讀取）
│   │   ├── src/auth_stub.py  # FastAPI Depends factory（修正版）
│   │   ├── src/audit_stub.py # AuditLog 完整 mirror admin.AuditLog 9 欄位
│   │   ├── alembic/          # fresh baseline migration（不搬歷史 25 支）
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── worker/               # 新增：背景任務 worker（dream / slow-thinker / sleeptime / reindex）
│   │   ├── main.py           # 共用 src/，不同 entrypoint
│   │   └── Dockerfile        # 同 api image base，CMD 改 worker
│   ├── web/                  # React 前端
│   │   ├── src/modules/memvault/  # 從 workbench/src/modules/memvault/ 搬（30 個 .tsx）
│   │   ├── src/shared/api/        # 從 workbench/src/shared/api/ 搬最小子集（axios + interceptor）
│   │   ├── src/shared/types/      # 共用 type
│   │   ├── src/shared/utils/      # formatters / date
│   │   ├── src/shared/journal/    # TanStack Query + ActionJournal middleware
│   │   ├── Dockerfile        # multi-stage build → Nginx
│   │   └── package.json
│   └── embed-gateway/        # 三軌統一 HTTP 介面
│       ├── server.py         # FastAPI /embed → 後端路由（onnx / vllm / mlx-proxy）
│       ├── backends/
│       │   ├── onnx_runtime.py   # CPU fallback（自寫 Qwen3 wrapper 或 mxbai 替代）
│       │   ├── vllm_proxy.py     # 轉發到 vLLM container
│       │   └── mlx_proxy.py      # 轉發到 host MLX sidecar
│       └── Dockerfile
├── infra/
│   ├── docker-compose.yml         # 主堆疊（pin image versions）
│   ├── docker-compose.mac.yml     # Mac override（不啟 embed container，走 host MLX）
│   ├── docker-compose.gpu.yml     # GPU override（embed 換 vLLM）
│   ├── docker-compose.frozen.yml  # 可選 override（啟 MinIO + frozen tier）
│   ├── postgres/init.sql          # CREATE SCHEMA memvault
│   ├── qdrant/config.yaml
│   ├── litellm/config.yaml        # LiteLLM model_list
│   ├── mlx-sidecar/               # Mac LaunchAgent + embed_worker.py
│   └── nginx/                     # 前端 + reverse proxy 配置
├── scripts/
│   ├── install.sh            # macOS / Linux 一鍵（含 OS+GPU 三軌偵測）
│   ├── install.ps1           # Windows
│   ├── generate-secrets.sh   # 產生 .env
│   ├── preflight.sh          # docker / port / disk 檢查
│   ├── post-install.html     # 安裝完成導引頁（auto-open）
│   ├── doctor.sh             # 新增：memvault doctor（檢查 docker/ports/embed/llm/migration/qdrant）
│   ├── upgrade.sh            # 新增：版本升級（pull + migrate + restart，含 dry-run）
│   ├── backup.sh             # 新增：dump postgres + qdrant snapshot + .env
│   ├── restore.sh            # 新增：從 backup 還原
│   └── uninstall.sh          # 新增：down + rm volumes（互動確認）
├── .env.example
├── README.md / README.zh.md
└── docs/
    ├── architecture.md
    ├── api.md
    ├── upgrade.md            # 升級指南
    ├── backup-restore.md     # 備份/還原指南
    └── troubleshooting.md
```

### Container 清單（v2，pin version + internal network + worker 拆分）

**Network 收斂原則**：只有 `web` 與 `api` expose 到 host port (127.0.0.1)，其他全部 internal-only。

**Pin 策略**：所有 image 在 `compose.yml` 直接寫成 `${IMAGE}@${DIGEST}` 的環境變數形式，digest 由 CI 跑 `scripts/pin-images.sh` 定期 `docker buildx imagetools inspect` 取得寫回 `.env.example`（同時保留 fallback tag 註解供人類查閱）。**不允許 `latest` / `main` / `main-stable` / `RELEASE.xxx-x` 範圍版本**。

| Container | Image (pinned via digest) | Expose | 必需 | 說明 |
|-----------|---------------------------|--------|------|------|
| `memvault-api` | 自建（Python 3.12 + uv），ghcr.io/operonlab/memvault-api:${VERSION}@${API_DIGEST} | `127.0.0.1:8080` | ✅ | FastAPI 全 66 routes |
| `memvault-worker` | 同 api image，CMD=worker | (internal) | ✅ | dream / slow-thinker / sleeptime / reindex / backfill |
| `memvault-web` | 自建 Nginx + 靜態檔，ghcr.io/operonlab/memvault-web:${VERSION}@${WEB_DIGEST} | `127.0.0.1:3000` | ✅ | React UI |
| `postgres` | `pgvector/pgvector:0.8.0-pg16@${PG_DIGEST}` | (internal) | ✅ | Vector + 全文搜尋 |
| `redis` | `redis:7.4.1-alpine@${REDIS_DIGEST}` | (internal) | ✅ | Cache + 排程 |
| `qdrant` | `qdrant/qdrant:v1.12.4@${QDRANT_DIGEST}` | (internal) | ✅ | Hybrid search |
| `embed-gateway` | 自建，ghcr.io/operonlab/memvault-embed-gateway:${VERSION}@${EMBED_DIGEST} | (internal) | ✅ | API 永遠打它；mlx-proxy/vllm-proxy/onnx 三後端切換 |
| `litellm` | `ghcr.io/berriai/litellm:v1.55.10@${LITELLM_DIGEST}` | **(internal only)** | ✅ | LLM 聚合層；安裝時強制配至少一條 provider key |
| `vllm` | `vllm/vllm-openai:v0.6.4.post1@${VLLM_DIGEST}` | (internal) | ⭕ | 僅 GPU profile 啟用 |
| `minio` | `minio/minio:RELEASE.2024-12-13T22-19-12Z@${MINIO_DIGEST}` | (internal) | ⭕ | 僅 frozen tier profile 啟用 |

**Compose override 矩陣**：
- 預設：`docker-compose.yml`（embed-gateway 用 ONNX 後端）
- macOS：`docker-compose.yml + docker-compose.mac.yml`（embed-gateway 切 mlx-proxy 後端，連 host MLX sidecar）
- GPU：`docker-compose.yml + docker-compose.gpu.yml`（啟 vllm container，embed-gateway 切 vllm-proxy 後端）
- Frozen tier：附加 `docker-compose.frozen.yml`（啟 minio）

**所有 image 在 `.env.example` 中以 `*_DIGEST` 變數聲明**（命名與 compose.yml 中變數一致），CI 跑 `pin-images.sh` 定期更新 digest，使用者可重現。

### 跨 OS 安裝腳本設計

#### macOS / Linux：`install.sh`

```
curl -fsSL https://memvault-os.dev/install.sh | bash
```

流程（採 Supabase「強制產生 secrets」+ Dify「post-install HTML」混合策略）：

1. **OS 偵測** — `uname` → macOS/Linux 分支
2. **Pre-flight 檢查**（任一失敗即中止並給修補指引）：
   - Docker binary 存在（沒有 → macOS 提示 `brew install --cask docker`，Linux 給 `https://get.docker.com` 一行 script）
   - Docker daemon running（`docker info` 不通 → 提示打開 Docker Desktop）
   - **Port 8080 / 3000 沒人佔**（只檢查 host-exposed port；postgres/redis/qdrant/litellm/embed-gateway 全 internal-only，不需要也不應該檢查 host port）
   - 衝突 → 互動式問是否改 host port → 寫進 `.env` `WEB_PORT` / `API_PORT`
   - 磁碟空間 ≥ 5GB
3. **Clone / 下載** — `git clone --depth 1 operonlab/memvault-os` 或 download tarball
4. **產生 .env** — 呼叫 `generate-secrets.sh`：
   - `POSTGRES_PASSWORD` = `openssl rand -base64 24`
   - `MEMVAULT_SECRET_KEY` = `openssl rand -base64 32`
   - `REDIS_PASSWORD` = `openssl rand -base64 18`
   - `LITELLM_MASTER_KEY` = `openssl rand -base64 24`
5. **LLM 強制配置（必選一條，無「跳過」選項）**：
   - 互動式選單：
     1. OpenAI（輸入 `OPENAI_API_KEY`）
     2. Anthropic（輸入 `ANTHROPIC_API_KEY`）
     3. Google Gemini（輸入 `GEMINI_API_KEY`）
     4. DeepSeek（輸入 `DEEPSEEK_API_KEY`）
     5. 本地 Ollama（後續 installer 自動 pull `qwen2.5:7b`，配置 LiteLLM 指向 `host.docker.internal:11434`）
   - 至少一條成功通過 **「真實 model call smoke test」** 才往下：
     - 先打 `litellm:4000/health/liveliness` 確認 proxy 起來（必要但不充分）
     - 再打 `litellm:4000/v1/chat/completions` body=`{"model":"<user-selected-alias>","messages":[{"role":"user","content":"ping"}],"max_tokens":4}` 必須回 200 + 非空 content
     - 失敗 → 顯示具體錯訊（401/403=key 錯、429=配額用完、超時=網路），互動式重新選 provider
   - Linux Docker 設 `extra_hosts: ["host.docker.internal:host-gateway"]`（macOS / Windows Desktop 已內建，Linux 需顯式宣告）
5. **拉 image** — `docker compose pull`（顯示進度）
6. **啟動** — `docker compose up -d`
7. **健康檢查輪詢** — 最多 90s 等所有 service `healthy`
8. **Migration** — `docker compose exec api alembic upgrade head`
9. **完成導引** — 寫 `post-install.html`（含 URL / default account / .env 備份提醒）→ `open` / `xdg-open` 自動開瀏覽器

#### Windows：`install.ps1`

```powershell
irm https://memvault-os.dev/install.ps1 | iex
```

差異點：
- WSL2 偵測（`wsl --list --verbose`）— 沒裝 → 提示 `wsl --install` 後重啟
- Docker Desktop 偵測（`docker version` exit code）
- Port 檢查用 `Get-NetTCPConnection`
- secrets 產生改用 PowerShell：`[System.Web.Security.Membership]::GeneratePassword(32, 8)` 或 `python -c "import secrets..."` fallback
- `start` 取代 `open` 開 HTML

#### Pre-flight 共用矩陣

| 檢查項 | 阻斷? | 修補指引 |
|--------|-------|---------|
| Docker 已裝 | ✅ | macOS: `brew install --cask docker`; Linux: `curl -fsSL https://get.docker.com \| sh`; Windows: 開 Docker Desktop 下載頁 |
| Docker daemon | ✅ | 提示啟動 Docker Desktop / `sudo systemctl start docker` |
| **Host port 8080 / 3000 空閒**（其他全 internal） | ✅（可換 port） | 互動式問新 port → 寫 .env `WEB_PORT` / `API_PORT` |
| 磁碟 ≥5GB | ⚠️（warn） | `df -h` 顯示，使用者確認後繼續 |
| Docker version ≥24.0 | ⚠️ | compose v2 + `host-gateway` 支援需求 |
| Windows: WSL2 distro | ✅ | `wsl --install -d Ubuntu` |
| Linux: 可使用 `host.docker.internal` | ⚠️ | 不通 → 自動加 `extra_hosts: host-gateway`（Docker 24.0+ 內建支援） |
| RAM ≥4GB | ⚠️ | LiteLLM + Qdrant + Postgres 同時跑會吃記憶體 |
| **至少一條 LLM provider 已配置** | ✅ | LLM 互動式選單跳出強制選一條 |

### 解耦工作清單（v2，吃下 codex 二審）

| # | 動作 | 檔案 / 位置 | 工作量 |
|---|------|-----------|-------|
| **0a** | **Phase 0.1 freeze** `docs/route_manifest.yaml`（66 routes）+ `docs/schema_manifest.yaml`（16 tables + audit_logs 9 欄位） | docs/ | S |
| **0b** | **Phase 0.2** 修 embedding 4 處 runtime drift（kg_routes.py:74,500,513 + services.py:995）→ 全走 Qdrant | memvault/*.py | M |
| **0c** | **Phase 0.3** 寫 `events_stub/bus.py`、`events_stub/types.py`、`config_stub.py` | apps/api/src/ | M |
| **0d** | **Phase 0.4** 盤點前端 `@/api/client` / `@/types` / `@/shared/utils` / `@/shared/journal` 實際依賴並抽最小子集 | docs/web_dependency_inventory.md | S |
| 1 | 建新 repo `operonlab/memvault-os` 骨架 + worktree path | — | S |
| 2 | 搬 `core/src/modules/memvault/` 全部 81 檔 | apps/api/src/memvault/ | S |
| 3 | **抽自家最小公共層 `apps/api/src/shared/`**（不照搬 Workshop src/shared/）：qdrant_client, qdrant_search, embedding_client, redis, database, cache, reactive, text_utils, access_tracker, rlm_engine、移除 auth/admin 命名殘留 | apps/api/src/shared/ | **L** |
| 4 | **Fresh baseline migration**：`alembic revision --autogenerate`（`include_schemas=True`，import models.py + kg_models.py + audit_stub.py），與 `schema_manifest.yaml` 比對 | apps/api/alembic/versions/0001_init.py | M |
| 5 | 改 `kg_routes.py:12` `from src.modules.auth.permissions` → `from src.auth_stub` | 1 行 | S |
| 5b | **改 BaseCRUDService**（`core/src/shared/services.py:260`）：`from src.modules.admin.models` → `from src.audit_stub`，欄位完全保持（mirror 9 欄位） | services.py | S |
| 6 | `events.py` 5 條 reactive flow：保留 memvault 內部事件，外部 source（capture.* / intelligence.*）改成 webhook 接收端 + no-op fallback | events.py + 新增 `webhooks.py` | M |
| 7 | `llm_config.py` / `qdrant_client.py`：所有 hardcode → `os.getenv()` | 4 個 hardcode 點 | S |
| 8 | `omlx_bridge.py` → `embedding_client.py`：HTTP POST 到 `EMBED_BASE_URL`（embed-gateway 統一介面） | 重寫 ~60 行 | M |
| 9a | **[少爺指定優先]** `infra/mlx-sidecar/embed_worker.py` HTTP 版 + LaunchAgent plist | ~80 行 + plist | S |
| 9b | `docker-compose.gpu.yml` + `vllm` container 定義（`vllm/vllm-openai:v0.6.4.post1@${VLLM_DIGEST}`，`--task embed`） | ~40 行 YAML | S |
| 9c | **`apps/embed-gateway/`** 三軌統一介面：`server.py` + `backends/{onnx_runtime,vllm_proxy,mlx_proxy}.py`。CPU fallback **採自寫 ONNX Runtime wrapper 跑 Qwen3-Embedding-0.6B ONNX**（保證 1024d 跨機向量可攜），備援方案 `mxbai-embed-large-v1` | apps/embed-gateway/ | **M-L** |
| 9d | `infra/litellm/config.yaml`（5-6 model alias，pin `v1.55.x`） | ~40 行 YAML | S |
| 9e | `apps/api/src/auth_stub.py` **FastAPI Depends 正確形式** + `audit_stub.py`（最小 audit table + ENABLED flag） | ~80 行 | S |
| 9f | **新增 `apps/worker/`**：dream / slow_thinker / sleeptime / reindex / backfill 入口；shared codebase，不同 entrypoint | apps/worker/main.py | M |
| 9g | **Frozen tier 處理**：加 `docker-compose.frozen.yml` + MinIO container 或 `MEMVAULT_FROZEN_TIER=disabled` flag；改 `services.py` / `routes.py` 偵測 flag 路由到 MinIO endpoint | apps/api + infra/ | M |
| 10 | 抽前端 `workbench/src/modules/memvault/` → 獨立 Vite/Rsbuild app | apps/web/ | M |
| 11 | 寫 Dockerfile（api/worker/web/embed-gateway）+ 4 個 compose 檔（base / mac / gpu / frozen） | infra/ | M |
| 12 | `install.sh` / `install.ps1`（含 OS+GPU 三軌偵測 + LLM 強制配置互動）+ `generate-secrets.sh` + `preflight.sh` + `post-install.html` | scripts/ | L |
| 12b | **`scripts/doctor.sh`**：檢查 docker / ports / embed-gateway / litellm / postgres / qdrant / migration head 一致性 | ~150 行 | M |
| 12c | **`scripts/upgrade.sh`**：pull 新 image + 跑 alembic upgrade head + restart，含 `--dry-run` | ~80 行 | M |
| 12d | **`scripts/backup.sh` / `restore.sh`**：pg_dump + qdrant snapshot + .env tar | ~120 行 | M |
| 12e | **`scripts/uninstall.sh`**：互動確認 → `compose down -v` + 清 launchd plist (Mac) | ~60 行 | S |
| 12f | **`scripts/pin-images.sh`**（CI 跑）：pull image → 取 digest → 寫回 `.env.example` 的 `*_DIGEST` | ~50 行 | S |
| 13 | `.env.example`（含所有 `*_DIGEST` 變數）+ README（中英雙語）+ docs/upgrade.md / backup-restore.md | 根目錄 | S |
| 14 | LICENSE（MIT）+ CI（GitHub Actions：lint + test + build image push to ghcr + 自動 pin digest） | .github/workflows/ | M |

### Verification（v2，端到端 + lifecycle 操作）

#### 基礎驗收
1. **Lint**：`ruff check apps/api/ apps/worker/ apps/embed-gateway/`、`biome check apps/web/`
2. **Backend test**：`docker compose exec api pytest -m "not integration"`
3. **Auth stub 注入驗證**（codex 修正項）：寫一個 pytest，hit 任一 `require_permission` 路由，確認 FastAPI 正確注入 `_user` dict（不是 coroutine）
4. **Fresh baseline migration**：clean Postgres → `alembic upgrade head` → `psql -c "\dt memvault*"` 驗證 ~16 張表都在 + `audit_logs` 在
5. **Audit flag 切換**：設 `MEMVAULT_AUDIT_ENABLED=false` → 對 block CRUD → 確認 `audit_logs` 沒寫入
6. **Embed-gateway 三軌一致性**（codex 重點）：
   - 同一段文字 `"the quick brown fox"` 過 MLX / vLLM / ONNX 三後端
   - 比對 cosine similarity ≥ 0.99（保證跨機向量可攜，不會破 Qdrant collection）
7. **Qdrant 連通**：`curl http://qdrant:6333/collections`（internal network 內測）
8. **LiteLLM 健康**：`curl litellm:4000/health` 至少一條 provider 通
9. **API smoke**：`curl 127.0.0.1:8080/api/memvault/blocks` 回 200 + 空 list
10. **Worker 健康**：`docker compose logs memvault-worker | grep "scheduler started"`
11. **前端**：開 `127.0.0.1:3000`，建一筆 block → galaxy 看到節點

#### 跨 OS 安裝（5 種組合）
| 組合 | OS | 硬體 | embed 後端 |
|------|----|------|-----------|
| 1 | macOS 14 Apple Silicon | Docker Desktop | MLX host sidecar via launchd |
| 2 | Ubuntu 22.04 | NVIDIA GPU + nvidia-container-toolkit | vLLM container |
| 3 | Ubuntu 22.04 | 無 GPU | embed-gateway ONNX 後端 |
| 4 | Windows 11 | Docker Desktop + WSL2 + WSL CUDA | vLLM container |
| 5 | Windows 11 | Docker Desktop + WSL2，無 GPU | embed-gateway ONNX 後端 |

每種組合都跑：`install.{sh,ps1}` → `doctor.sh` → 健康檢查全綠 → galaxy 顯示節點。

#### Lifecycle 操作驗收（codex 新增項）
12. **`scripts/doctor.sh`**：人為破壞一個服務（停 redis container）→ 跑 doctor → 必須點出 redis 不通、給修補命令
13. **`scripts/upgrade.sh --dry-run`**：印出將要 pull 的 image diff、將要跑的 alembic head 變化，不實際執行
14. **`scripts/backup.sh` → `restore.sh`** 端到端：建 10 筆 block → backup → drop volumes → restore → 10 筆全在
15. **`scripts/uninstall.sh`**：互動確認 → 跑完 → `docker volume ls` 沒殘留、Mac launchd plist 已 unload

#### 完整功能驗收（少爺要求完整 66 routes）
16. **dream loop**：建 5 筆 block → 觸發 dream → KG triples 生成 → galaxy 顯示節點+邊
17. **slow-thinker**：query 走 query.completed event → shadow metric 寫入
18. **frozen tier**（若啟 `docker-compose.frozen.yml`）：archive 一筆 block → MinIO 有 `s3://memvault/blocks/...` → `/frozen/{id}/thaw` 回得到原文

#### 安全 / 可重現性驗收（codex 重點）
19. **Internal network**：`docker port memvault-postgres` / `redis` / `qdrant` 應為空（不 expose）
20. **Image 全 pinned**：`grep -E "image:.*:(latest|main|main-stable)$" infra/docker-compose*.yml` 必須無命中
21. **重複安裝同 commit hash**：兩台機器跑 `install.sh` → `docker images --digests` 比對應一致

### 少爺已定的設計選擇（取代待決事項）

| 抉擇 | 選擇 | 影響 |
|------|------|------|
| Embedding | **OS-conditional 三軌**：Mac→MLX、Linux/Windows + GPU→vLLM、無 GPU→ONNX Runtime（自寫 Qwen3 wrapper） | install script 須做 OS+GPU 偵測，分發三種 compose profile |
| LLM | **內建 LiteLLM proxy** + 多 provider 支援 | `litellm` container 改 required（非 optional profile），使用者填任一 provider key 即可 |
| Auth | **Single-user mode V1** | `require_permission()` stub 直接放行，預設 `space_id="default"`、`user_id="local"` |
| 範圍 | **完整 66 routes** | KG / dream / slow-thinker / 5 條 reactive flow 全保留 |

### Embedding OS-Conditional 三軌設計（關鍵）

#### Tier 1：macOS (Apple Silicon) — MLX host sidecar

MLX 需要 Metal GPU 直存，**不能跑在 Docker 內**（Docker Desktop on Mac 是 Linux VM，沒 Metal 權限）。所以：

- **架構**：MLX embed_worker 跑在 Mac host（用 `launchd` LaunchAgent 管），bind `127.0.0.1:18081`（避開 Docker 8081）
- **API 永遠打 `embed-gateway:8081`（internal network）** — embed-gateway 內部 `mlx_proxy` backend 才透過 `host.docker.internal:18081` 連 host MLX sidecar；API 端不直接知道後端是誰
- **embed-gateway compose.mac.yml override**：只有 embed-gateway container 帶 `extra_hosts: ["host.docker.internal:host-gateway"]`，並啟用 `EMBED_BACKEND=mlx_proxy` env var
- **install.sh 動作**：
  1. 偵測 `uname -m` = `arm64` + macOS
  2. `pip install mlx-embeddings`（用 `~/.venvs/memvault-mlx`）
  3. 寫 `~/Library/LaunchAgents/dev.memvault.embed.plist` 並 `launchctl load`
  4. compose 用 `docker-compose.yml + docker-compose.mac.yml`（embed-gateway 啟，但內部走 mlx_proxy）

#### Tier 2：Linux/Windows + NVIDIA GPU — vLLM container

- **偵測**：`nvidia-smi` exit 0 + `docker run --rm --gpus all nvidia/cuda:12.2.0-base nvidia-smi` 通
- **Compose override** `docker-compose.gpu.yml`：
  ```yaml
  vllm:
    image: vllm/vllm-openai:v0.6.4.post1@${VLLM_DIGEST}
    command: --model Qwen/Qwen3-Embedding-0.6B --task embed --port 8000
    expose: ["8000"]   # internal-only
    deploy:
      resources:
        reservations:
          devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]
  embed-gateway:
    environment:
      - EMBED_BACKEND=vllm_proxy
      - VLLM_BASE_URL=http://vllm:8000/v1
  ```
- **前置依賴**：Linux 需 `nvidia-container-toolkit`；Windows 需 WSL2 + WSL CUDA
- **install script** 偵測到 GPU 後自動寫 `COMPOSE_PROFILES=gpu`

#### Tier 3：Linux/Windows 無 GPU — ONNX Runtime CPU fallback

- **預設 fallback**，讓「任何電腦都能跑」的承諾成立
- **Image**：自建 `apps/embed-gateway/Dockerfile`，內含 `onnxruntime` + 自寫 Qwen3-Embedding-0.6B ONNX wrapper（1024d，**與 MLX / vLLM 同一份 HF safetensors 權重來源，cosine similarity ≥ 0.99**，保證 Qdrant collection 跨機可攜）
- **備援方案**：若 Qwen3 ONNX 轉檔不順，CPU 改用官方 1024d 模型 `mxbai-embed-large-v1`（中英效果需重測，整個 corpus 必須一次性 reindex）
- **介面相容**：`POST /embed {"texts": [...], "task_type": ...}` → `{"embeddings": [...]}`，跟 MLX/vLLM 三後端輸出格式統一
- **效能**：CPU 上約 80-150 ms / 32 texts batch，個人使用無感

#### 統一 client 抽象

`apps/api/src/shared/embedding_client.py`：
- 讀 `EMBED_BASE_URL` 環境變數，**所有平台一律 `http://embed-gateway:8081`**（internal network）
- API 端不知道也不需要知道後端是 MLX / vLLM / ONNX；切換由 embed-gateway 內部 `EMBED_BACKEND` env var 決定
- 取代原 `omlx_bridge.py` 的 stdin/stdout subprocess 協定

`apps/embed-gateway/server.py` 路由邏輯：
```python
import os
BACKEND = os.getenv("EMBED_BACKEND", "onnx")  # mlx_proxy / vllm_proxy / onnx
@app.post("/embed")
async def embed(req: EmbedRequest):
    if BACKEND == "mlx_proxy":
        return await mlx_proxy.forward(req)   # → host.docker.internal:18081
    if BACKEND == "vllm_proxy":
        return await vllm_proxy.forward(req)  # → vllm:8000
    return await onnx_runtime.embed(req)      # 內建 ONNX Runtime
```

### 安裝腳本三軌偵測流程

```
install.sh / install.ps1 主流程：

[ OS 偵測 ]
├── macOS arm64 ───────► [ MLX 路線 ]
│                       ├── 建 ~/.venvs/memvault-mlx
│                       ├── pip install mlx-embeddings
│                       ├── 寫 LaunchAgent plist
│                       ├── launchctl load
│                       └── COMPOSE_FILE=compose.yml:compose.mac.yml
│
├── Linux ─┬── nvidia-smi 通 ───► [ vLLM 路線 ]
│         │                     ├── 檢查 nvidia-container-toolkit
│         │                     └── COMPOSE_FILE=compose.yml:compose.gpu.yml
│         └── 否 ───────────────► [ ONNX Runtime 路線 ]
│                                 └── COMPOSE_FILE=compose.yml
│
└── Windows ─┬── WSL2 + nvidia-smi 通 ─► [ vLLM 路線 ]
            └── 否 ──────────────────► [ ONNX Runtime 路線 ]
```

### LiteLLM proxy（取代原 optional profile）

升格為必需 container：

```yaml
litellm:
  image: ghcr.io/berriai/litellm:v1.55.10@${LITELLM_DIGEST}
  # No ports: expose — internal network only, accessed by api/worker via http://litellm:4000/v1
  expose: ["4000"]
  environment:
    - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
    - OPENAI_API_KEY=${OPENAI_API_KEY:-}
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
    - GEMINI_API_KEY=${GEMINI_API_KEY:-}
    - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
  extra_hosts: ["host.docker.internal:host-gateway"]  # for local Ollama option
  volumes: ["./infra/litellm/config.yaml:/app/config.yaml:ro"]
  command: --config /app/config.yaml --port 4000
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:4000/health/liveliness"]
    interval: 30s
    retries: 3
```

`infra/litellm/config.yaml` 預設提供 5-6 個 model alias（gpt-4o-mini / claude-haiku / gemini-flash / deepseek-chat / qwen-flash），使用者填 .env 即生效。**未配置 API key 的 alias 由 LiteLLM 自動隱藏**（不出現在 `/v1/models` 清單），但 installer 在 LLM 強制配置階段已驗證至少一條 alias 通過真實 model call smoke test，不會出現「裝完 0 個可用 model」的狀態。

memvault `llm_config.py` 改為 `LITELLM_BASE=http://litellm:4000/v1`、`LITELLM_KEY=${LITELLM_MASTER_KEY}`，所有 LLM call 走這條。

### Auth Stub（Single-User V1，FastAPI Depends 正確形式）

⚠️ **修正 codex 二審指出的錯誤**：原計畫 `async def require_permission()` 會被 FastAPI 當 coroutine default value，**Depends 不會注入**。`has_permission` 簽名也須跟 monorepo 對齊（接 `role: str`，不是 user dict）。

新增 `apps/api/src/auth_stub.py`：

```python
from fastapi import Depends
from typing import Annotated

LOCAL_USER = {
    "id": "local",
    "email": "local@memvault",
    "role": "admin",
    "permissions": ["*"],
    "space_id": "default",
}

async def _current_user() -> dict:
    """V1 single-user：always returns the local synthetic user."""
    return LOCAL_USER

def require_permission(scope: str):
    """Factory returning a FastAPI Depends — matches monorepo signature.
    Usage in routes: `_user: dict = Depends(require_permission("memvault.read"))`
    or kept as `_user: dict = require_permission("memvault.read")` if monorepo
    already wraps with Depends inside the call site.
    """
    async def _dep(user: Annotated[dict, Depends(_current_user)]) -> dict:
        # V1：放行所有 scope；V2 升級點檢查 scope ∈ user["permissions"]
        return user
    return Depends(_dep)

def has_permission(role: str, scope: str) -> bool:
    """Match monorepo signature: (role: str, scope: str) -> bool.
    V1 single-user：admin 永遠 True。"""
    return role == "admin" or "*" in scope
```

`kg_routes.py:12` 改 `from src.auth_stub import has_permission`，**不用改 call site**。所有 routes.py 既有的 `_user: dict = require_permission("memvault.read")` 維持原樣（因為新 factory 直接 return `Depends(...)`）。

預留 V2 升級點：env var `MEMVAULT_AUTH_MODE=single|cookie|api_token`，cookie 模式接 itsdangerous + Postgres users 表，api_token 模式吃 `MEMVAULT_API_TOKEN` 單一 token。

---

### Audit Stub（取代 admin.AuditLog 依賴）

⚠️ codex 指出 `BaseCRUDService` import `src.modules.admin.models.AuditLog`，獨立後直接炸。

⚠️ **codex 三審指出原計畫 stub 欄位與 `BaseCRUDService._record_audit()` 不相容**。實際簽名（`core/src/shared/services.py:247-274`）：

```python
async def _record_audit(self, db, action: str, entity_id: str,
                        user_id: str | None = None, space_id: str | None = None,
                        changes: dict | None = None, snapshot: dict | None = None)
# log = AuditLog(id, user_id, module, entity_type, entity_id, space_id, action, changes, snapshot)
```

新增 `apps/api/src/audit_stub.py`，**完整 mirror monorepo `src.modules.admin.models.AuditLog` 的欄位**（`core/src/modules/admin/models.py:23` 起）：

```python
import os
from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.shared.models import Base, TimestampMixin

ENABLED = os.getenv("MEMVAULT_AUDIT_ENABLED", "true").lower() == "true"

class AuditLog(Base, TimestampMixin):
    """Minimal audit — column types fully mirror monorepo admin.AuditLog
    (core/src/modules/admin/models.py:23-) so BaseCRUDService._record_audit()
    works without modification. Types verified 2026-04-28."""
    __tablename__ = "audit_logs"
    __table_args__ = (Index("idx_audit_space", "space_id"),
                      Index("idx_audit_entity", "entity_type", "entity_id"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid v7
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    module: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    space_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

**BaseCRUDService 改點**：v2 計畫的「BaseCRUDService import audit_stub」改成「`if ENABLED: from src.audit_stub import AuditLog else: return`」，原 `_record_audit()` 內部邏輯不變，只是 import source 從 `src.modules.admin.models` 換成 `src.audit_stub`。

Fresh baseline migration 包這張 `audit_logs` 表。預設 `MEMVAULT_AUDIT_ENABLED=true`；關掉 `_record_audit()` 整個 early-return。

<!-- v2 舊 container 清單表已刪除（被前段「Container 清單（v2，pin version + internal network + worker 拆分）」取代，避免雙清單矛盾） -->

**Note**：完整 container 清單請見上方「### Container 清單（v2，pin version + internal network + worker 拆分）」表，所有 image 已 pin 具體版本 + `${*_DIGEST}`。

---

## Phase 0：動工前清理項（Codex 三審指定）

⚠️ Codex 三審結論：「方向正確但有 v1 殘留」。動工前先做這 5 件事，確保不踩回舊坑。

### Phase 0.1 — 鎖 route manifest（V1 範圍 freeze）

驗證後實際 **66 routes / 81 檔（非測試）**，比 v1 計畫盤點的 49/48 多。

動作：
- 建 `docs/route_manifest.yaml`，列出全 66 個 route：method、path、scope、handler、保留/移除標記
- 全部標 **保留**（少爺指定完整功能），但 freeze 後 V1.1 / V1.2 才能新增或變更 path
- 同時 freeze 16 張 ORM table list 進 `docs/schema_manifest.yaml`，作為 fresh baseline migration 的 source of truth

### Phase 0.2 — 修 embedding ORM/runtime drift（採方案 A：全走 Qdrant）

**現況**：ORM `models.py` / `kg_models.py` 已無 `embedding` 欄位（pgvector → Qdrant 遷移殘留），但程式碼 4 處仍寫入/查 SA `.embedding`：

| 檔案 | 行 | 動作 |
|------|----|------|
| `kg_routes.py:74` | `instance.embedding = embedding` | 移除 SA assignment — 改 `index_document(IndexDocument(service_id="memvault", entity_id=instance.id, entity_type="triple", space_id=..., content=triple_text, tags=[...], metadata={"predicate": ...}))`，**embedding 由 index_document 內部呼叫 embed-gateway 產生**，呼叫者不直接傳向量 |
| `kg_routes.py:500` | `where(Triple.embedding.is_(None))` | Postgres 端不再有 embedding 欄位，改：取得 Qdrant client 透過 `qdrant_client.get_qdrant_client().scroll(collection_name="workshop-docs-1024", scroll_filter=Filter(must=[FieldCondition(key="service_id", match=MatchValue(value="memvault")), FieldCondition(key="entity_type", match=MatchValue(value="triple"))]), limit=...)`，列出已索引 entity_id；再跟 Postgres Triple 表 LEFT JOIN 找未索引者 |
| `kg_routes.py:513` | `t.embedding = emb` | 移除 — 改 `index_documents_batch([IndexDocument(service_id="memvault", entity_id=t.id, entity_type="triple", content=..., ...) for t in batch])` |
| `services.py:995` | `update(MemoryBlock).values(embedding=embedding)` | 移除 — 改 `index_document(IndexDocument(service_id="memvault", entity_id=block_id, entity_type="block", space_id=..., content=block.content, tags=block.tags, metadata={...}))` |

**Note**（驗證於 2026-04-28）：
- `core/src/shared/qdrant_search.py:94` 是 `index_document(doc)`，`:143` 是 `index_documents_batch(docs)`，`:401` 是 `delete_document`（不是 `scroll`）；**`qdrant_search.py` 沒有 `scroll` 公開函式**
- `IndexDocument` 真實欄位（`core/src/shared/search_types.py:8-20`）：`service_id, entity_id, entity_type, space_id, content, tags, created_at, updated_at, metadata`，**沒有 `vector=` 參數** — content-driven，內部自動產 embedding
- Scroll 操作要透過 `qdrant_client.get_qdrant_client().scroll(...)` 直接用底層 client，或在 `qdrant_search.py` 新增明確 `scroll_by_service` helper（建議後者，封裝 collection_name + 預設 filter）

**不重加 ORM 欄位**（避免雙寫一致性問題）。Qdrant 是 single source of truth for vectors。

### Phase 0.3 — 補 `src.events` / `src.config` 最小替代層

7+ 處 import 來源：

```
core/src/modules/memvault/events.py:9          from src.events.types import MemvaultEvents
core/src/modules/memvault/entity_resolution.py:17,18  from src.events.bus, types
core/src/modules/memvault/kg_auto_evolve.py:13,14
core/src/modules/memvault/kg_routes.py:10,641
core/src/modules/memvault/kg_services.py:16,17
core/src/modules/memvault/dream.py:803
```

新增：
- `apps/api/src/events_stub/bus.py` — in-process pub/sub（保留 memvault 內部事件用）
- `apps/api/src/events_stub/types.py` — 把 `MemvaultEvents` enum 從 monorepo `src.events.types` 抽出來；外部模組事件（`SessionIntelligenceEvents`、`CaptureEvents`）只保 enum 名 + no-op handler
- `apps/api/src/config_stub.py` — 替代 `src.config.settings`，從 env 讀少數必要設定（DB_URL、REDIS_URL、QDRANT_URL、EMBED_BASE_URL、LITELLM_BASE 等）

### Phase 0.4 — 前端依賴擴展（不只搬 modules/memvault）

`workbench/src/modules/memvault/` 還依賴：
- `@/api/client` — axios wrapper + auth interceptor
- `@/types` — 共用 type 定義
- `@/shared/utils/*` — formatters / date helpers
- `@/shared/journal/*` — TanStack Query + ActionJournal middleware（reactive 架構）

**`apps/web/` 額外要搬**：上述 4 條依賴的最小子集。新增 `apps/web/src/shared/{api,types,utils,journal}/` 跟著走。

### Phase 0.5 — 鎖 audit_logs 欄位契約

在 `docs/schema_manifest.yaml` 內 freeze `audit_logs` 表 9 個欄位（id, user_id, module, entity_type, entity_id, space_id, action, changes, snapshot），動工後不可改 schema（不然 BaseCRUDService 又會炸）。

---

## Codex 二審變更總覽（v1 → v2）

吃下 codex 二審 12 項修正：

### 硬錯誤修正
1. **Migration 數字**：v1 寫 7 支 / 8 表 → v2 改 fresh baseline migration（實際 ~25 支 / 16 表，重新 generate init）
2. **Auth stub 寫法**：v1 `async def require_permission(scope)` → v2 改 `Depends(...)` factory，避免 FastAPI 注入失敗
3. **`has_permission` 簽名**：v1 `(user: dict, scope)` → v2 `(role: str, scope: str)` 對齊 monorepo
4. **缺 admin.AuditLog 處理**：v2 新增 `audit_stub.py` + 最小 `audit_logs` 表 + `MEMVAULT_AUDIT_ENABLED` flag
5. **缺 frozen tier S3**：v2 新增 `docker-compose.frozen.yml` + MinIO container（pin version），或 `MEMVAULT_FROZEN_TIER=disabled` flag
6. **缺 worker 拆分**：v2 新增 `apps/worker/` container（dream / slow_thinker / sleeptime / reindex）

### CPU fallback 模型決策
7. **FastEmbed 不支援 Qwen3**：v2 改在 `apps/embed-gateway/backends/onnx_runtime.py` 自寫 ONNX Runtime wrapper 跑 Qwen3-Embedding-0.6B ONNX，保證 1024d 跨機向量可攜；備援方案 `mxbai-embed-large-v1`

### 安全 / 可重現性
8. **所有 image pin version**：用 `*_IMAGE_DIGEST` 環境變數 + `scripts/pin-images.sh` CI 自動更新；禁用 `latest` / `main` / `main-stable`
9. **Compose network 收斂**：Postgres / Redis / Qdrant / MinIO / LiteLLM / vllm / embed-gateway 全 internal-only；只有 web (3000) + API (8080) bind `127.0.0.1`
10. **LLM 強制可用**：installer 互動式至少配一條 provider key（OpenAI / Anthropic / Gemini / DeepSeek / 本地 Ollama 五選一），避免「裝好但 KG/dream 不能用」

### Lifecycle 操作（開源產品必備）
11. **新增 5 支 lifecycle 腳本**：`doctor.sh`（健診）/ `upgrade.sh`（升級含 --dry-run）/ `backup.sh`（pg_dump + qdrant snapshot）/ `restore.sh` / `uninstall.sh`（互動確認 + 清 launchd plist）

### 結構性
12. **shared/ 不照搬**：v1 想搬 Workshop `src/shared/` 子集 → v2 改建 `apps/api/src/shared/` 自家最小公共層，移除 auth/admin/events/config 命名殘留（codex 建議）

### 範圍堅持
- 少爺立場「**完整 66 routes**」維持不變（拒 codex 三層 MVP 建議），但承擔 +30% 工程量代價

### 不採納項
- codex 建議「V1.0 只 blocks/search/KG read-write」未採納，理由：少爺明確要求完整功能、避免 V1.0 釋出後使用者抱怨缺功能

---

## Codex 三審 → v3 變更（Phase 0 清理）

吃下 codex 三審 6 項 ⚠️ + 4 項漏雷：

### 6 項 ⚠️ 修正
1. **Audit stub 欄位對齊**：欄位完全 mirror `admin.AuditLog` 9 欄位（id, user_id, module, entity_type, entity_id, space_id, action, changes, snapshot），與 `BaseCRUDService._record_audit()` 簽名一致
2. **Image pin 殘留清理**：所有 `latest` / `main` / `main-stable` / `RELEASE.xxx-x` / `v0.6.x` 範圍版本 → 具體 version + `${*_DIGEST}` 環境變數
3. **Internal network 一致性**：preflight 只檢查 host port (8080/3000)，LiteLLM `expose:` 取代 `ports:`，全部 internal-only
4. **LLM 強制配置**：移除「跳過」選項，必選一條 provider key 或本地 Ollama，且 `litellm /health` 必須通才往下
5. **範圍盤點修正**：49 routes / 48 檔 → 實際 **66 routes / 81 檔**，新增 `route_manifest.yaml` freeze
6. **shared/ 補完**：除 utility 外，加 `events_stub/` + `config_stub.py` 替代層

### 4 項漏雷修正
7. **Embedding ORM/runtime drift**：採方案 A（全走 Qdrant），4 處 runtime bug 列入 Phase 0.2 必修清單
8. **embed-gateway 架構統一**：API 永遠打 `embed-gateway:8081`（internal），不直連 host；Mac 模式由 embed-gateway 內部 `mlx_proxy` backend 透過 `host.docker.internal:18081` 連 host MLX
9. **前端依賴擴展**：`apps/web/` 加 `shared/{api,types,utils,journal}/` 子集
10. **Linux extra_hosts**：`extra_hosts: ["host.docker.internal:host-gateway"]` 加在 `litellm` 與 `embed-gateway`，Linux Docker 24.0+ 內建支援

---

## Codex 三審 → v3.1 殘留清掃（最終）

吃下 codex 三審指出的 6 處 v1/v2 殘留 + 3 個 API 細節錯誤：

### v1/v2 殘留清掃
1. **重複 container 清單表**：刪除舊「Container 清單修正版（8 個）」表（含 latest/main-stable），統一用 v2 新表
2. **GPU compose 範例**：`vllm-openai:latest` → `v0.6.4.post1@${VLLM_DIGEST}`，並補 `expose: ["8000"]` + `embed-gateway` env override
3. **「FastEmbed」全清**：Tier 3 標題、install 三軌流程圖、CPU fallback 段落全改「ONNX Runtime」
4. **數字殘留**：`完整 49 routes` → `完整 66 routes`（驗收標題、決策表、總覽各一處）；`48 檔` → `81 檔（非測試）`
5. **變數命名統一**：`*_IMAGE_DIGEST` → `*_DIGEST`（兩處）
6. **vLLM 9b 任務描述**：`pin v0.6.x` → `v0.6.4.post1@${VLLM_DIGEST}`

### API / 型別細節修正
7. **audit_stub 型別 mirror**：`module/entity_type/action` 從 `String(64)` 改 `Text`，`changes/snapshot` 從 `JSON` 改 `JSONB`，`entity_id` 從 `String(64)` 改 `String(32)`，與 `core/src/modules/admin/models.py:23-` 一致
8. **Qdrant API 修正**：Phase 0.2 原引用 `qdrant_search.upsert()` 函式不存在；改 `index_document(IndexDocument(...))` / `index_documents_batch([...])` / `scroll`，與 `core/src/shared/qdrant_search.py:94,143,401` 真實 API 一致
9. **LLM smoke test 強化**：原只檢查 `litellm /health/liveliness`（liveness check 不證 provider key 可用）→ 加實際 `POST /v1/chat/completions` 真實 model call，必須回 200 + 非空 content 才算通

### 結論
v3.1 = **真正 ready 動工**。Codex 三審判決：「主設計可進 Phase 0；第一個 commit 只做計畫書去殘留 + manifest freeze + embedding drift 修正，再開始抽 repo 會穩很多」。本次清掃已執行此建議。

---

## Codex 四審 → v3.2 最終實作契約修正

吃下 codex 四審指出的 3 個實作契約錯誤：

1. **`IndexDocument` 簽名修正**：v3.1 寫的 `IndexDocument(..., vector=embedding, ...)` **錯了** — 真實簽名（`core/src/shared/search_types.py:8-20`）是 content-driven，沒有 `vector=` 參數，欄位是 `service_id, entity_id, entity_type, space_id, content, tags, created_at, updated_at, metadata`。`index_document()` 內部呼叫 embed-gateway 自動產 embedding。Phase 0.2 表格已全改。
2. **`scroll` 不在 qdrant_search.py**：v3.1 引用「`qdrant_search.scroll`」**不存在** — line 401 是 `delete_document`。改用 `qdrant_client.get_qdrant_client().scroll(...)` 底層 client，或新增 `scroll_by_service` helper（建議後者）。
3. **audit 表名統一**：v3.1 stub 是 `audit_logs`，但 verification 段寫 `memvault_audit_logs` — 全 plan 統一為 `audit_logs`。
4. **bit-level 過強**：「bit-level 與 MLX/vLLM 一致」改「同一份 HF safetensors 權重來源，cosine similarity ≥ 0.99」（vLLM PagedAttention / ONNX kernel 數值上不可能 bit-level 一致，但 cosine 高足以保證 Qdrant 索引相容）。
5. **Critical files 修辭**：「重寫成 fastembed HTTP client」→「重寫成 embed-gateway HTTP client」。

### v3.2 判定
**Codex 四審通過後即為 v3.2，真正 fully ready 動工**。第一個 commit 仍是 Phase 0（manifest freeze + embedding drift + events/config stub），不直接進入 repo scaffold / compose / installer。

---

## Critical files referenced

- `/Users/joneshong/workshop/core/src/modules/memvault/*.py` — **81 檔（非測試）**，整批搬
- `/Users/joneshong/workshop/core/src/shared/qdrant_client.py` + `qdrant_search.py` — 抽出，環境變數化
- `/Users/joneshong/workshop/core/src/shared/embedding.py` + `omlx_bridge.py` — 重寫成 embed-gateway HTTP client（`apps/api/src/shared/embedding_client.py`）
- `/Users/joneshong/workshop/core/src/modules/memvault/llm_config.py` — env var 化
- `/Users/joneshong/workshop/core/src/modules/memvault/kg_routes.py:12` — 換 auth stub
- `/Users/joneshong/workshop/infra/docker/docker-compose.yml` — base 範本
- `/Users/joneshong/workshop/infra/docker/init.sql` — schema 初始化
- `/Users/joneshong/workshop/stations/envkit/bootstrap/phase1-infra.sh` — installer idempotency 範本
- `/Users/joneshong/workshop/workbench/src/modules/memvault/` — 前端拆出
- `/Users/joneshong/workshop/core/migrations/versions/` — **總 56 支 migration / 71 個檔涉及 memvault keywords / ~25 支直接 touch memvault tables**；不搬歷史鏈，採 fresh baseline 重新 autogenerate
- `/Users/joneshong/workshop/core/src/shared/services.py` — `BaseCRUDService` 內 `from src.modules.admin.models import AuditLog` 必須改 `audit_stub`
