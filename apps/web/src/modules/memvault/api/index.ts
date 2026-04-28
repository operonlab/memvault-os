import { buildParams, createCrudApi, request } from '@/api/client'
import type {
  KASProfile,
  MemoryBlock,
  MemoryBlockCreate,
  MemoryBlockUpdate,
  PaginatedResponse,
  SemanticSearchResult,
} from '@/types'
import type { MemoryInspectResponse, MemoryQueryOptions, MemoryQueryResponse } from '../types'

export interface SyncScanResult {
  total: number
  synced: number
  failed: number
  skipped: number
  already: number
  log: string
}

export interface SyncStats {
  total: number
  synced: number
  failed: number
  skipped: number
}

const USE_MOCK =
  (import.meta as unknown as { env: Record<string, string> }).env.VITE_USE_MOCK === 'true'

const crudApi = createCrudApi<MemoryBlock, MemoryBlockCreate, MemoryBlockUpdate>('/memvault/blocks')

const realApi = {
  ...crudApi,

  searchSemantic: (query: string, topK = 10): Promise<SemanticSearchResult[]> =>
    request<SemanticSearchResult[]>(
      `/memvault/search?q=${encodeURIComponent(query)}&top_k=${topK}`,
    ),

  queryMemory: (
    query: string,
    options: Partial<MemoryQueryOptions> = {},
  ): Promise<MemoryQueryResponse> =>
    request<MemoryQueryResponse>('/memvault/query', {
      method: 'POST',
      body: JSON.stringify({
        q: query,
        task_mode: options.taskMode ?? 'build',
        thinking_mode: options.thinkingMode ?? 'auto',
        load_budget: options.loadBudget ?? 'standard',
        consumer: options.consumer ?? 'human',
        top_k: options.topK ?? 6,
      }),
    }),

  inspectMemory: (
    query: string,
    options: Partial<MemoryQueryOptions> = {},
  ): Promise<MemoryQueryResponse> =>
    request<MemoryInspectResponse>('/memvault/inspect', {
      method: 'POST',
      body: JSON.stringify({
        q: query,
        task_mode: options.taskMode ?? 'reflect',
        thinking_mode: 'slow',
        load_budget: options.loadBudget ?? 'deep',
        consumer: options.consumer ?? 'ui',
        top_k: options.topK ?? 8,
      }),
    }).then((data) => ({
      query: data.query,
      strategy: data.strategy,
      fast_cards: [],
      working_cards: [],
      deep_cards: data.cards.filter((card) => card.layer === 'deep'),
      highlights: data.cards
        .filter((card) => card.layer === 'deep')
        .slice(0, 2)
        .map((card) => card.use_now),
      metadata: {
        ...(data.metadata ?? {}),
        raw_sections: data.raw_sections,
      },
    })),

  getProfile: (): Promise<KASProfile> => request<KASProfile>('/memvault/profile'),

  listByTag: (tag: string, page = 1, pageSize = 20): Promise<PaginatedResponse<MemoryBlock>> =>
    request<PaginatedResponse<MemoryBlock>>(
      `/memvault/blocks?tag=${encodeURIComponent(tag)}&page=${page}&page_size=${pageSize}`,
    ),

  listByType: (
    blockType: string,
    page = 1,
    pageSize = 20,
  ): Promise<PaginatedResponse<MemoryBlock>> =>
    request<PaginatedResponse<MemoryBlock>>(
      `/memvault/blocks?block_type=${encodeURIComponent(blockType)}&page=${page}&page_size=${pageSize}`,
    ),

  listBlocks: (
    page = 1,
    pageSize = 20,
    filters: { tag?: string | null; blockType?: string | null } = {},
  ): Promise<PaginatedResponse<MemoryBlock>> =>
    request<PaginatedResponse<MemoryBlock>>(
      `/memvault/blocks${buildParams({
        page,
        page_size: pageSize,
        tag: filters.tag ?? undefined,
        block_type: filters.blockType ?? undefined,
      })}`,
    ),

  syncScan: (recent?: number): Promise<SyncScanResult> =>
    request<SyncScanResult>(`/memvault/sync/scan${recent ? `?recent=${recent}` : ''}`, {
      method: 'POST',
    }),

  syncStats: (): Promise<SyncStats> => request<SyncStats>('/memvault/sync/stats'),

  recalculateProfile: (): Promise<KASProfile> =>
    request<KASProfile>('/memvault/profile/recalculate', { method: 'POST' }),
}

import { mockMemvaultApi } from './mock'

const api = USE_MOCK ? mockMemvaultApi : realApi
export { api as memvaultApi }
export { kgApi } from './kg'
