# Memvault Web — Dependency Inventory

Phase 0.4 audit ahead of extracting `workbench/src/modules/memvault/` from the monorepo
into a standalone OSS repo. Goal: enumerate every cross-boundary import (anything outside
`workbench/src/modules/memvault/`) so we know exactly which files must travel along, get
inlined, or be replaced.

Source paths below are relative to `/Users/joneshong/workshop/workbench/`.
Module files scanned: 39 (`.tsx` / `.ts`).

## 1. Cross-boundary imports

### 1.1 `@/api/*` — HTTP client

| Source path | Used by | Purpose | Must port? |
|-------------|---------|---------|-----------|
| `src/api/client.ts` (`request`, `createCrudApi`, `buildParams`, `ApiError`) | `memvault/api/index.ts:1`, `memvault/api/kg.ts:1` | fetch wrapper with retry, CRUD factory, query-string builder | YES (fetch-only, swap to plain fetch or keep as-is) |

`client.ts` itself imports `@/types` (`ErrorResponse`, `PaginatedResponse`) and
`@/shared/utils/retry` (`withRetry`) — transitively required.

### 1.2 `@/types/*` — domain types

| Source path | Used by | Purpose | Must port? |
|-------------|---------|---------|-----------|
| `src/types/index.ts` (`MemoryBlock`, `MemoryBlockCreate`, `MemoryBlockUpdate`, `KASProfile`, `PaginatedResponse`, `ErrorResponse`, `BaseEntity`, `SemanticSearchResult`) | `memvault/api/index.ts:9`, `memvault/api/kg.ts:2`, `memvault/api/mock.ts:8`, `memvault/components/MemoryCard.tsx:1`, `memvault/components/ProfileWidget.tsx:1`, `memvault/components/UnderstandingGauge.tsx:1`, `memvault/hooks/mutations.ts:2`, `memvault/hooks/useGalaxy.ts:2`, `memvault/pages/browser.tsx:3`, `memvault/pages/galaxy.tsx:2`, `memvault/stores/index.ts:4` | shared TS interfaces (memvault types already live here, just relocate) | YES — extract memvault-related types only |

Non-memvault types in the same file (`User`, `AppInfo`) can stay behind; only
`BaseEntity`, `PaginatedResponse`, `ErrorResponse`, `MemoryBlock*`, `KASProfile`,
`SemanticSearchResult` move.

### 1.3 `@/shared/utils/*` — utilities

| Source path | Used by | Purpose | Must port? |
|-------------|---------|---------|-----------|
| `src/shared/utils/actionJournal.ts` (`logMutation`, `journal`) | `memvault/hooks/mutations.ts:3` | append-only frontend action log | OPTIONAL (drop if OSS doesn't need replay/audit; otherwise port as-is) |
| `src/shared/utils/journalMiddleware.ts` (`withJournal`) | `memvault/stores/index.ts:3` | Zustand middleware that records named actions | OPTIONAL (paired with actionJournal) |
| `src/shared/utils/retry.ts` (`withRetry`) | transitively via `client.ts` | exponential-backoff retry for idempotent fetches | YES if porting client.ts |

### 1.4 `../../../shared/utils/time` — relative-path util

| Source path | Used by | Purpose | Must port? |
|-------------|---------|---------|-----------|
| `src/shared/utils/time.ts` (`relativeTime`) | `memvault/components/AttitudeTimeline.tsx:2`, `memvault/components/KgExplorerPanel.tsx:2`, `memvault/components/MemoryCard.tsx:2`, `memvault/components/SessionCard.tsx:2`, `memvault/pages/galaxy.tsx:3` | "5 分鐘前" verbose Chinese relative time formatter | YES (12 lines, inline into module utils) |

Note: only the `relativeTime` function is used; `timeAgo` is unused by memvault.

### 1.5 `@/shared/*` aliased — none beyond utils

No memvault file imports from `@/shared/components`, `@/shared/stores`, or
`@/shared/hooks`. Good — no shared component coupling.

### 1.6 `@/components/*` / `@/lib/*` — none

Memvault module is fully self-contained for components and lib helpers.

## 2. Third-party npm dependencies (memvault-side)

Bare-package imports inside `memvault/`:

| Package | Used by memvault? | Used by other modules? | OSS-pkg required? |
|---------|------------------|------------------------|-------------------|
| `react`, `react-dom` | YES | all | YES |
| `react-router-dom` | YES | all | YES |
| `@tanstack/react-query` | YES | most | YES |
| `zustand` (+ `zustand/middleware`) | YES | most | YES |
| `lucide-react` | YES | most | YES |
| `three` | YES (`GalaxyCanvas.tsx`) | also `anvil/SkillGalaxyCanvas.tsx` | YES |
| `3d-force-graph` | YES (`GalaxyCanvas.tsx`) | also `anvil/SkillGalaxyCanvas.tsx` | YES |

**Memvault-exclusive npm deps:** none. `three` + `3d-force-graph` are shared with
`anvil` but in the OSS extraction context they are still required runtime deps for
the galaxy view. `@react-three/fiber` and `@react-three/drei` declared in
`workbench/package.json` are **NOT** used by memvault (zero references) — exclude
from OSS package.json.

## 3. Minimum portable subset (path list)

Files that must accompany `workbench/src/modules/memvault/` into the OSS repo
(absolute paths under `/Users/joneshong/workshop/workbench/`):

```
src/api/client.ts                          # request, createCrudApi, buildParams, ApiError
src/shared/utils/retry.ts                  # withRetry — transitive via client.ts
src/shared/utils/time.ts                   # relativeTime (or inline 12 lines into module)
src/types/index.ts                         # extract: BaseEntity, PaginatedResponse, ErrorResponse,
                                           #          MemoryBlock, MemoryBlockCreate, MemoryBlockUpdate,
                                           #          KASProfile, SemanticSearchResult
src/shared/utils/actionJournal.ts          # OPTIONAL — only if action replay/audit retained
src/shared/utils/journalMiddleware.ts      # OPTIONAL — paired with actionJournal
```

Hard subset = 4 files (`client.ts`, `retry.ts`, `time.ts`, types extract).
Full subset (with action journaling) = 6 files.

## 4. Recommended OSS `package.json` deps (memvault-only)

```json
{
  "dependencies": {
    "3d-force-graph": "^1.79.1",
    "@tanstack/react-query": "^5.95.2",
    "lucide-react": "^0.575.0",
    "react": "^19",
    "react-dom": "^19",
    "react-router-dom": "^6 || ^7",
    "three": "^0.183.1",
    "zustand": "^5"
  },
  "devDependencies": {
    "@types/three": "^0.183.1"
  }
}
```

Excluded vs `workbench/package.json`: `@dnd-kit/*`, `@react-three/drei`,
`@react-three/fiber`, `@xyflow/react`, `react-grid-layout`, `react-markdown`,
`recharts`, `remark-gfm`, `@workshop/ai-assistant` (none used by memvault).
