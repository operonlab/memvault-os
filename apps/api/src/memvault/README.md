# Memvault 模組（後端）

> Memvault — PostgreSQL + Qdrant 混合搜尋引擎（BM25 + dense vector）驅動的持久化記憶引擎。

## 定位

Workshop Core 的 `memvault` 模組，負責 Claude Code 的持久化記憶管理。以 Workshop 架構原則建構。

## 核心能力

| 能力 | 說明 |
|------|------|
| **記憶提煉** | SessionEnd Hook → LLM 提煉對話 → 結構化記憶區塊 |
| **混合搜尋** | Keyword（BM25）+ Vector（Qdrant cosine similarity）+ RRF 融合 |
| **知識域晉升** | 高頻 tag 自動聚合為 Knowledge Domain |
| **Profile Score** | 三維量化：Knowledge / Attitude / Skill |
| **Galaxy 資料** | 生成三維星系圖資料供前端視覺化 |
| **多 Agent 態度** | 不同 Agent 可帶有不同 Attitude Profile 進行協作 |

## DB Schema（`memvault` schema）

```sql
-- 記憶區塊（核心表）
CREATE TABLE memvault.blocks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    TEXT NOT NULL,
    topic         TEXT NOT NULL,
    type          TEXT NOT NULL,          -- decision / technical / achievement / pattern / ...
    tags          TEXT[] NOT NULL,
    project       TEXT,
    content       TEXT NOT NULL,
    embedding     vector(768),            -- legacy storage, search via Qdrant
    quality_score FLOAT DEFAULT 0,
    space_id      UUID NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

-- 知識域
CREATE TABLE memvault.knowledge_domains (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    block_count INT DEFAULT 0,
    depth_score FLOAT DEFAULT 0,
    embedding   vector(768),
    space_id    UUID NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- Profile Score
CREATE TABLE memvault.profile_scores (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id        UUID UNIQUE NOT NULL,
    knowledge       JSONB NOT NULL DEFAULT '{}',   -- domains + depth
    attitude        JSONB NOT NULL DEFAULT '{}',   -- risk, decision_style, communication
    skills          JSONB NOT NULL DEFAULT '{}',   -- verified skills + success_rate
    memory_stats    JSONB NOT NULL DEFAULT '{}',   -- total_blocks, quality_score, coverage
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- 索引
CREATE INDEX idx_blocks_tags ON memvault.blocks USING GIN (tags);
CREATE INDEX idx_blocks_embedding ON memvault.blocks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_blocks_created ON memvault.blocks (created_at DESC);
CREATE INDEX idx_blocks_session ON memvault.blocks (session_id);
CREATE INDEX idx_domains_embedding ON memvault.knowledge_domains USING hnsw (embedding vector_cosine_ops);
```

## API 端點（`/api/memvault/`）

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/blocks` | 列表（支援 tag 過濾、時間範圍、分頁） |
| GET | `/blocks/:id` | 單筆記憶區塊 |
| POST | `/blocks` | 建立記憶區塊（SessionEnd Hook 呼叫） |
| PUT | `/blocks/:id` | 更新記憶區塊 |
| DELETE | `/blocks/:id` | 刪除記憶區塊 |
| POST | `/recall` | 混合搜尋（keyword + vector + RRF） |
| POST | `/extract` | 手動提煉 session transcript |
| GET | `/domains` | 知識域列表 |
| POST | `/domains/promote` | 將高頻 tag 晉升為知識域 |
| GET | `/profile` | Profile Score |
| POST | `/profile/rebuild` | 重建 Profile Score |
| GET | `/galaxy` | Galaxy 視覺化資料 |
| POST | `/embeddings/sync` | 批量同步 embeddings |
| GET | `/stats` | 統計（block 數量、tag 分佈、embedding 覆蓋率） |

## 目錄結構（規劃）

```
core/src/modules/memvault/
├── README.md           ← 本文件
├── __init__.py
├── routes.py           ← API 路由
├── schemas.py          ← Pydantic models
├── models.py           ← SQLAlchemy models
├── service.py          ← 業務邏輯
├── search.py           ← 混合搜尋引擎（BM25 + Qdrant + RRF）
├── extractor.py        ← LLM 提煉引擎（Gemini Flash + Haiku pipeline）
├── galaxy.py           ← Galaxy 資料生成
└── events.py           ← 事件定義（memvault.block.created 等）
```

## 遷移計劃

1. 建立 schema + models → 匯入現有 ~700+ memory blocks
2. 實作 `/recall` API（復刻現有 RRF 搜尋邏輯）
3. 實作 `/extract` API（替代 extract.sh 中的 LLM 管線）
4. 切換 SessionEnd Hook 端點
5. 切換 MCP Server 為 Core API 薄適配器

## 相依模組

- **auth** — space_id 隔離
- **mcp/memvault** — MCP 工具對接

## Skill 整合

除了 memvault MCP 外，以下 Skill 的產出可作為記憶來源：

| Skill | 整合方式 |
|-------|---------|
| **meeting-insights** | 溝通模式分析結果作為 memvault block 寫入，追蹤溝通風格演變 |

## 參考

- V1 (已遷移)：`~/Claude/projects/kas-memory/`
- MCP Server：`~/workshop/mcp/memvault/`
- Galaxy 視覺化（V1，已遷移）：`~/Claude/memvault/galaxy-explorer.html`
- 現有 meeting-insights skill：`~/.claude/skills/meeting-insights/SKILL.md`
