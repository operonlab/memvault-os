import type {
  KASProfile,
  MemoryBlock,
  MemoryBlockCreate,
  MemoryBlockUpdate,
  PaginatedResponse,
  SemanticSearchResult,
} from '@/types'
import type { MemoryCardRecord, MemoryQueryOptions, MemoryQueryResponse } from '../types'

const delay = (ms = 50) => new Promise<void>((r) => setTimeout(r, ms))

let mockBlocks: MemoryBlock[] = [
  {
    id: '01915a2b3c4d5e6f7a8b9c0d1e2f3a4b',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'pgvector 的 HNSW 索引可以加速向量搜尋',
    block_type: 'knowledge',
    tags: ['database', 'postgresql', 'vector-search'],
    source_session: 'sess_20260115_001',
    confidence: 0.92,
    created_at: '2026-01-15T08:30:00Z',
    updated_at: '2026-01-15T08:30:00Z',
  },
  {
    id: '01915b3c4d5e6f7a8b9c0d1e2f3a4b5c',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'FastAPI 的 Dependency Injection 支援 async generators',
    block_type: 'knowledge',
    tags: ['fastapi', 'python', 'dependency-injection'],
    source_session: 'sess_20260118_003',
    confidence: 0.88,
    created_at: '2026-01-18T14:20:00Z',
    updated_at: '2026-01-20T09:00:00Z',
  },
  {
    id: '01915c4d5e6f7a8b9c0d1e2f3a4b5c6d',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Redis Stream 作為輕量級事件匯流排，支援 Consumer Group 實現多消費者競爭消費',
    block_type: 'knowledge',
    tags: ['redis', 'event-driven', 'messaging'],
    source_session: null,
    confidence: 0.85,
    created_at: '2026-01-22T11:00:00Z',
    updated_at: '2026-01-22T11:00:00Z',
  },
  {
    id: '01915d5e6f7a8b9c0d1e2f3a4b5c6d7e',
    space_id: 'default_space',
    created_by: 'user_001',
    content: '使用 Playwright 進行端對端測試',
    block_type: 'skill',
    tags: ['testing', 'e2e', 'playwright'],
    source_session: 'sess_20260120_007',
    confidence: 0.95,
    created_at: '2026-01-20T16:45:00Z',
    updated_at: '2026-01-21T10:30:00Z',
  },
  {
    id: '01915e6f7a8b9c0d1e2f3a4b5c6d7e8f',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Zustand 搭配 immer middleware 管理複雜狀態',
    block_type: 'skill',
    tags: ['react', 'zustand', 'state-management'],
    source_session: 'sess_20260122_002',
    confidence: 0.91,
    created_at: '2026-01-22T09:15:00Z',
    updated_at: '2026-01-23T08:00:00Z',
  },
  {
    id: '01915f7a8b9c0d1e2f3a4b5c6d7e8f90',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Canvas 2D API 實作 force-directed graph，以 requestAnimationFrame 驅動動畫迴圈',
    block_type: 'skill',
    tags: ['canvas', 'visualization', 'graph', 'animation'],
    source_session: null,
    confidence: 0.78,
    created_at: '2026-01-25T13:00:00Z',
    updated_at: '2026-01-25T13:00:00Z',
  },
  {
    id: '019160818b9c0d1e2f3a4b5c6d7e8f901',
    space_id: 'default_space',
    created_by: 'user_001',
    content: '偏好漸進式重構而非全面重寫',
    block_type: 'attitude',
    tags: ['refactoring', 'engineering-values'],
    source_session: 'sess_20260110_005',
    confidence: 0.97,
    created_at: '2026-01-10T10:00:00Z',
    updated_at: '2026-01-10T10:00:00Z',
  },
  {
    id: '0191618b9c0d1e2f3a4b5c6d7e8f9012',
    space_id: 'default_space',
    created_by: 'user_001',
    content: '優先使用成熟 OSS 而非自建輪子',
    block_type: 'attitude',
    tags: ['engineering-values', 'oss'],
    source_session: 'sess_20260112_001',
    confidence: 0.96,
    created_at: '2026-01-12T08:00:00Z',
    updated_at: '2026-01-14T09:30:00Z',
  },
  {
    id: '01916292c0d1e2f3a4b5c6d7e8f90123',
    space_id: 'default_space',
    created_by: 'user_001',
    content: '程式碼審查要關注可讀性與可維護性，而不只是功能正確性',
    block_type: 'attitude',
    tags: ['code-review', 'engineering-values'],
    source_session: null,
    confidence: 0.89,
    created_at: '2026-01-16T15:30:00Z',
    updated_at: '2026-01-16T15:30:00Z',
  },
  {
    id: '0191639ad1e2f3a4b5c6d7e8f901234',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Workshop 專案的模組邊界設計原則',
    block_type: 'general',
    tags: ['workshop', 'architecture', 'module-boundaries'],
    source_session: 'sess_20260101_001',
    confidence: 0.99,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-20T12:00:00Z',
  },
  {
    id: '019164a1e2f3a4b5c6d7e8f9012345',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Catppuccin Mocha 配色方案的 CSS 變數命名',
    block_type: 'general',
    tags: ['css', 'design-tokens', 'catppuccin'],
    source_session: null,
    confidence: 0.82,
    created_at: '2026-01-08T11:00:00Z',
    updated_at: '2026-01-08T11:00:00Z',
  },
  {
    id: '019165b2f3a4b5c6d7e8f90123456',
    space_id: 'default_space',
    created_by: 'user_001',
    content: 'Tailscale 用於遠端存取私有服務，替代傳統 port forwarding',
    block_type: 'general',
    tags: ['networking', 'tailscale', 'devops'],
    source_session: 'sess_20260105_002',
    confidence: 0.93,
    created_at: '2026-01-05T09:30:00Z',
    updated_at: '2026-01-05T09:30:00Z',
  },
]

const mockProfile: KASProfile = {
  id: 'profile_default_space_001',
  space_id: 'default_space',
  knowledge_score: 72,
  attitude_score: 85,
  skill_score: 68,
  updated_at: '2026-02-24T00:00:00Z',
}

function paginate<T>(items: T[], page: number, pageSize: number): PaginatedResponse<T> {
  const start = (page - 1) * pageSize
  const end = start + pageSize
  return {
    items: items.slice(start, end),
    total: items.length,
    page,
    page_size: pageSize,
  }
}

function buildCard(block: MemoryBlock, layer: 'fast' | 'working' | 'deep'): MemoryCardRecord {
  return {
    id: `${layer}:${block.id}`,
    title: `${block.block_type} / ${block.tags[0] ?? 'memory'}`,
    summary: block.content,
    why_relevant: 'Mock data matched by content or tag.',
    use_now: `Use this ${layer} card as current context.`,
    layer,
    source_type: block.block_type,
    confidence: block.confidence,
    freshness: '近兩週',
    tags: block.tags,
    evidence_refs: [
      {
        kind: 'block',
        ref_id: block.id,
        title: block.block_type,
        snippet: block.content.slice(0, 80),
        score: block.confidence,
      },
    ],
  }
}

export const mockMemvaultApi = {
  list: async (page = 1, pageSize = 20): Promise<PaginatedResponse<MemoryBlock>> => {
    await delay()
    return paginate(mockBlocks, page, pageSize)
  },

  get: async (id: string): Promise<MemoryBlock> => {
    await delay()
    const block = mockBlocks.find((b) => b.id === id)
    if (!block) return Promise.reject(new Error(`MemoryBlock not found: ${id}`))
    return block
  },

  create: async (data: MemoryBlockCreate): Promise<MemoryBlock> => {
    await delay()
    const now = new Date().toISOString()
    const newBlock: MemoryBlock = {
      id: `mock_${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`,
      space_id: 'default_space',
      created_by: 'user_001',
      content: data.content,
      block_type: data.block_type,
      tags: data.tags ?? [],
      source_session: data.source_session ?? null,
      confidence: 1.0,
      created_at: now,
      updated_at: now,
    }
    mockBlocks = [...mockBlocks, newBlock]
    return newBlock
  },

  update: async (id: string, data: MemoryBlockUpdate): Promise<MemoryBlock> => {
    await delay()
    const index = mockBlocks.findIndex((b) => b.id === id)
    if (index === -1) return Promise.reject(new Error(`MemoryBlock not found: ${id}`))
    const updated: MemoryBlock = {
      ...mockBlocks[index],
      ...data,
      updated_at: new Date().toISOString(),
    }
    mockBlocks = [...mockBlocks.slice(0, index), updated, ...mockBlocks.slice(index + 1)]
    return updated
  },

  delete: async (id: string): Promise<void> => {
    await delay()
    const index = mockBlocks.findIndex((b) => b.id === id)
    if (index === -1) return Promise.reject(new Error(`MemoryBlock not found: ${id}`))
    mockBlocks = mockBlocks.filter((b) => b.id !== id)
  },

  searchSemantic: async (query: string, topK = 10): Promise<SemanticSearchResult[]> => {
    await delay()
    const lower = query.toLowerCase()
    const results = mockBlocks
      .map((block) => {
        const contentMatch = block.content.toLowerCase().includes(lower)
        const tagMatch = block.tags.some((t) => t.toLowerCase().includes(lower))
        const score = contentMatch
          ? 0.85 + Math.random() * 0.1
          : tagMatch
            ? 0.6 + Math.random() * 0.15
            : 0
        return { block, score }
      })
      .filter((r) => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, topK)
    return results
  },

  queryMemory: async (
    query: string,
    options: Partial<MemoryQueryOptions> = {},
  ): Promise<MemoryQueryResponse> => {
    const semantic = await mockMemvaultApi.searchSemantic(query, options.topK ?? 6)
    const blocks = semantic.map((item) => item.block)
    return {
      query,
      strategy: {
        task_mode: options.taskMode ?? 'build',
        thinking_mode_requested: options.thinkingMode ?? 'auto',
        thinking_mode_used:
          options.thinkingMode === 'slow' || options.loadBudget === 'deep' ? 'slow' : 'fast',
        load_budget: options.loadBudget ?? 'standard',
        consumer: options.consumer ?? 'human',
      },
      fast_cards: blocks.slice(0, 3).map((block) => buildCard(block, 'fast')),
      working_cards: blocks.slice(0, 2).map((block) => buildCard(block, 'working')),
      deep_cards: blocks.slice(0, 4).map((block) => buildCard(block, 'deep')),
      highlights: blocks.slice(0, 2).map((block) => block.content),
      metadata: { backend: 'mock' },
    }
  },

  inspectMemory: async (
    query: string,
    options: Partial<MemoryQueryOptions> = {},
  ): Promise<MemoryQueryResponse> => mockMemvaultApi.queryMemory(query, { ...options, thinkingMode: 'slow' }),

  getProfile: async (): Promise<KASProfile> => {
    await delay()
    return mockProfile
  },

  listByTag: async (
    tag: string,
    page = 1,
    pageSize = 20,
  ): Promise<PaginatedResponse<MemoryBlock>> => {
    await delay()
    const filtered = mockBlocks.filter((b) => b.tags.includes(tag))
    return paginate(filtered, page, pageSize)
  },

  listByType: async (
    blockType: string,
    page = 1,
    pageSize = 20,
  ): Promise<PaginatedResponse<MemoryBlock>> => {
    await delay()
    const filtered = mockBlocks.filter((b) => b.block_type === blockType)
    return paginate(filtered, page, pageSize)
  },

  listBlocks: async (
    page = 1,
    pageSize = 20,
    filters: { tag?: string | null; blockType?: string | null } = {},
  ): Promise<PaginatedResponse<MemoryBlock>> => {
    await delay()
    let filtered = mockBlocks
    if (filters.tag) filtered = filtered.filter((b) => b.tags.includes(filters.tag!))
    if (filters.blockType) filtered = filtered.filter((b) => b.block_type === filters.blockType)
    return paginate(filtered, page, pageSize)
  },

  syncScan: async () => {
    await delay(500)
    return {
      total: 12,
      synced: 0,
      failed: 0,
      skipped: 0,
      already: 12,
      log: '[mock] all up-to-date',
    }
  },

  syncStats: async () => {
    await delay()
    return { total: 12, synced: 12, failed: 0, skipped: 0 }
  },
}
