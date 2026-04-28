// --- Shared base types (extracted from workshop monorepo for memvault-os) ---

export interface BaseEntity {
  id: string
  space_id: string
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface ErrorResponse {
  detail: string
  code: string
  module: string | null
}

// --- Memvault API types ---

export interface MemoryBlock extends BaseEntity {
  content: string
  block_type: 'knowledge' | 'skill' | 'attitude' | 'general'
  tags: string[]
  source_session: string | null
  confidence: number
}

export interface MemoryBlockCreate {
  content: string
  block_type: 'knowledge' | 'skill' | 'attitude' | 'general'
  tags?: string[]
  source_session?: string
}

export interface MemoryBlockUpdate {
  content?: string
  block_type?: 'knowledge' | 'skill' | 'attitude' | 'general'
  tags?: string[]
  confidence?: number
}

export interface KASProfile {
  id: string
  space_id: string
  knowledge_score: number
  attitude_score: number
  skill_score: number
  updated_at: string
}

export interface SemanticSearchResult {
  block: MemoryBlock
  score: number
}
