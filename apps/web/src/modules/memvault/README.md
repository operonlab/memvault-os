# Memvault 模組（前端）

> KAS Galaxy 視覺化 + 記憶瀏覽器 — Memvault 模組的 Workbench UI。

## 頁面規劃

| 路由 | 頁面 | 說明 |
|------|------|------|
| `/memvault` | 記憶總覽 | KAS Profile 四維雷達圖 + 最近記憶 |
| `/memvault/galaxy` | KS Galaxy | Knowledge-Skill 星系圖（互動式 3D / 2D 視覺化） |
| `/memvault/blocks` | 記憶瀏覽器 | 記憶區塊列表（搜尋 + tag 過濾 + 時間線） |
| `/memvault/blocks/:id` | 記憶詳情 | 單筆記憶區塊 + 相關記憶推薦 |
| `/memvault/domains` | 知識域 | 知識域列表 + 深度分析 |

## Workbench Widgets

| Widget | 尺寸 | 說明 |
|--------|------|------|
| KAS Profile | 2x2 | 四維雷達圖（K/A/S/M） |
| 最近記憶 | 2x1 | 最近 5 筆記憶區塊摘要 |
| Galaxy Mini | 2x2 | 星系圖縮影（僅顯示核心節點） |

## 技術選型

- **Galaxy 視覺化**：Three.js（3D）或 D3.js force-directed（2D），視效能需求選擇
- **狀態管理**：Zustand（module-scoped store）
- **API 通訊**：`/api/memvault/*`，使用共用 `useApi` hook

## 目錄結構（規劃）

```
workbench/src/modules/memvault/
├── README.md             ← 本文件
├── index.tsx             ← 模組入口（導出路由）
├── pages/
│   ├── Overview.tsx      ← /memvault
│   ├── Galaxy.tsx        ← /memvault/galaxy
│   ├── BlockList.tsx     ← /memvault/blocks
│   ├── BlockDetail.tsx   ← /memvault/blocks/:id
│   └── Domains.tsx       ← /memvault/domains
├── components/
│   ├── KASRadar.tsx      ← 四維雷達圖
│   ├── GalaxyView.tsx    ← 星系圖視覺化
│   ├── BlockCard.tsx     ← 記憶區塊卡片
│   └── TagCloud.tsx      ← Tag 雲
├── widgets/
│   ├── KASProfileWidget.tsx
│   ├── RecentBlocksWidget.tsx
│   └── GalaxyMiniWidget.tsx
├── hooks/
│   └── useMemvault.ts    ← Memvault API hooks
├── stores/
│   └── memvaultStore.ts  ← Zustand store
├── api/
│   └── client.ts         ← Memvault API client
└── types/
    └── index.ts          ← Memvault types
```
