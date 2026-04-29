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

## ✅ 目前狀態：v1.0.1 — install 流程強化

v1.0.0 stack 是會動的，但 fresh-clone install 一路上踩了好幾個無聲陷阱。
v1.0.1 把它們收斂掉，目標是**讓不懂技術的 macOS 使用者只要 `git clone` 並執行一條指令**就能裝起來。

| 項目 | 現況 |
|------|------|
| Fresh-clone `install.sh` 端到端（macOS Apple Silicon, 真實 PTY 互動） | ✅ 已驗 — 8 / 8 容器啟動、alembic 18 張表、Web UI HTTP 200 |
| 真實 E2E HTTP 測試 | ✅ **42 / 42** 全綠（v1.0.0 是 40 / 42） |
| ghcr.io 預編 image | ✅ 公開 — `ghcr.io/operonlab/memvault-{api,web,embed-gateway}:1.0.0`（v1.0.1 git tag 落地時 CI 會自動 build & push 新 `1.0.1` tag） |
| Install 流程 regression test 在 CI | ✅ 11 條靜態檢查防止 install path 退化 |
| Codex adversarial review (v1.0.0) | ✅ 6 條（2 critical / 3 high / 1 medium）全修 |
| 離線模式安裝（無 LLM key） | ✅ 不填 key 也能完整裝起來，之後再補 key + `doctor.sh` 復檢 |
| Linux 端到端驗證 | ⚠️ 預編 image 已知可用；install 腳本只在 macOS 跑過 |
| Windows install.ps1 端到端 | ⚠️ 結構與 macOS 對齊但尚未實機跑過 |

### 自 v1.0.0 以來修補的 11 個 install 阻塞點（編號 A–H、J–L，刻意跳過字母「I」避免和數字「1」混淆）

每一條都有對應的靜態 regression test：

- **A** preflight 偵測到 port 衝突、要求使用者輸入新 port，但 `.env` 還沒被建立 → `write_env_port` 靜默 return，新 port 被吞掉。
- **B** secrets 用 base64 產生，含 `+` `/` `=`，會破 `postgresql://` / `redis://` URL parsing。
- **C** LLM smoke test 在 `pin-images.sh` 之前就 `docker compose up -d litellm`，但 `LITELLM_DIGEST=sha256:000…` 還沒被補真實 digest，pull 必失敗 `manifest unknown`。
- **D** `configure_llm` 沒有「跳過」選項，沒 LLM key 的新使用者卡死在無限 re-prompt。
- **E** `pin-images.sh` 試圖解析 `litellm:v1.55.10`（ghcr 從不存在這個 tag），而 compose 實際引用的是 `main-stable`。
- **F / G** `worker` 服務與 `wait_for_healthy` 都把 `litellm` 列為必要 healthy。但 `litellm:main-stable` 目前 prisma 啟動有回歸，常停在 `health: starting`。改為兩邊都把 litellm 當 best-effort。
- **H** `alembic upgrade head` 結束碼 0、log 印「Running upgrade」，**但完全沒建表**：`CREATE SCHEMA` 跑在 `context.begin_transaction()` 之外、auto-began transaction 沒被 alembic commit、async connection close 時 rollback。
- **J** LLM provider 選單 printf 走 stdout，但呼叫端用 `$()` 抓 stdout — 整個選單文字被當成 choice，case 永遠不對。
- **K** `read -r` 不剝 `\r`，PTY drivers 與 Windows CRLF stdin 留下的 `\r` 會破壞 case-match 與 regex-match。
- **L** `worker` 服務共用 api image，但 `apps/worker/` 從沒被 COPY 進去（`build.context: ../apps/api` 看不見 `apps/worker`）。

---

## 快速安裝（macOS / Linux）

### 前置

- Docker Desktop 24.0+
- 推薦 macOS Apple Silicon（Linux x86_64 應該也行；只有 macOS 完整端到端驗過）
- ≥ 5 GB 磁碟、≥ 4 GB RAM
- **裝起來不需要 LLM key** — 可以選離線模式之後再補。沒填 key 時 `litellm` 會停在 `health: starting`，`scripts/doctor.sh` 會識別這個狀態並把警告降為提示（不是 hard fail）。這是預期行為，不會卡其他服務。

### 一條指令

```bash
git clone https://github.com/operonlab/memvault-os.git
cd memvault-os
bash scripts/install.sh
```

過程是互動式的，每一個 prompt 都有合理的安全預設：

1. **Pre-flight** — 檢查 Docker、RAM、磁碟、host port。如果 `8080`(api) 或 `3000`(web) 已被占用，會問你要換到哪個 port，並寫回 `.env`。
2. **Secrets** — `POSTGRES_PASSWORD` / `REDIS_PASSWORD` / `MEMVAULT_SECRET_KEY` / `LITELLM_MASTER_KEY` 用 URL-safe hex 產生。
3. **Embedding 後端（自動偵測）** — Apple Silicon → MLX sidecar；NVIDIA GPU → vLLM container；其他 → ONNX Runtime CPU。
4. **Image 預備** — pin 第三方 digest，然後從 source 建 api / web / embed-gateway。
5. **LLM provider（互動）** — 選 `1) OpenAI`、`2) Anthropic`、`3) Gemini`、`4) DeepSeek`、`5) 本地 Ollama`，或**`6) 暫時跳過（離線模式）`**。選 6 stack 一樣會跑起來，只是 LLM 相關 endpoint（briefing / synth / triple-extract）會回 `503`，等你補 key 後就會正常。
6. **Compose up** — pull 第三方 image、起 stack、輪詢「必要服務」是否 healthy（postgres / redis / qdrant / embed-gateway / api）。litellm 是 best-effort 不阻擋。
7. **Alembic** — 套用 17 張表的 baseline migration，印出 `memvault schema 共 18 張表`。
8. **完成** — 開啟 `scripts/post-install.html`。Web UI: <http://localhost:3000>、API: <http://localhost:8080>（或你選的 port）。

### 之後再補 LLM key

```bash
# 編輯 .env，下面其中一條設好就行：
echo "OPENAI_API_KEY=sk-..." >> .env
# 或 ANTHROPIC_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY

docker compose restart litellm
bash scripts/doctor.sh   # 走過每個 service，回報綠/紅
```

### 非互動（CI / 自動化）

```bash
WEB_PORT=23000 API_PORT=28080 MEMVAULT_SKIP_LLM=1 bash scripts/install.sh
```

`MEMVAULT_SKIP_LLM=1`（或 `OFFLINE_MODE=1`）跳過互動式 provider 選單，並在 `.env` 寫 `MEMVAULT_LLM_DEFERRED=1`，這樣 `doctor.sh` 之後就知道要給友善的補救提示而不是 hard failure。

### 跑 E2E 測試

```bash
cd apps/api
uv venv .e2e-venv --python 3.12
uv pip install --python .e2e-venv/bin/python pytest pytest-asyncio httpx
MEMVAULT_TEST_BASE_URL=http://localhost:8080 \
  ./.e2e-venv/bin/python -m pytest tests/test_e2e_api.py -v
```

預期：**42 / 42 全綠**（macOS Apple Silicon 本機驗證 — CI 只跑 unit + install regression suite，e2e suite 需要真實 docker compose stack，留作 dev 驗證 / nightly job）。

---

## 架構快覽

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

- 只有 `web`(3000) 與 `api`(8080) bind 到 host，其他全在 internal `memvault-net` network。
- `embed-gateway` 容器依 `EMBED_BACKEND` 路由 embedding 請求到 MLX（host sidecar 透過 `host.docker.internal`）、vLLM（sidecar container）、或自帶的 ONNX Runtime。
- `worker` 容器跟 api 共用同一個 image，只是 CMD 改為 `python -m apps.worker.main` — 同一份 Python 源碼、同一份依賴，不需要第二個 image 維護。

---

## 功能特色（已實作）

- **66 個 REST endpoint** — block CRUD、混合搜尋、KG triples、社群偵測、recall、dream loop、slow-thinker
- **混合搜尋** — Qdrant dense + BM25 fusion + Postgres tsvector 全文 + CJK ILIKE
- **知識圖譜** — 自動演化 triples、實體解析、社群摘要、PPR retrieval
- **跨平台 embedding（三軌偵測）** — Apple Silicon 走 MLX、有 NVIDIA GPU 走 vLLM、其他平台 ONNX Runtime
- **多 LLM provider** — 內建 LiteLLM proxy，OpenAI / Anthropic / Gemini / DeepSeek 任選一條 key 配置
- **單人模式（Single-user V1）** — 沒 auth 包袱，雙擊跑起來自己用

---

## Roadmap（v1.0.1 後續）

- **Linux + Windows 端到端驗證** — 三軌 `install.sh` / `install.ps1` 路徑。
- **`scripts/configure-llm.sh`** — 引導式補 LLM key + smoke test，給選了離線模式的使用者。
- **`curl … | bash` 安裝路徑** — clone-from-curl 在腳本裡有但沒端到端測過。
- **Idempotent 重裝** — 在已有安裝上再跑一次 install.sh 應該乾淨收斂、不撞 volume。

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
