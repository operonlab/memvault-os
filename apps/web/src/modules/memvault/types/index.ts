export type BlockType = 'knowledge' | 'skill' | 'attitude' | 'general'

export type ViewMode = 'grid' | 'list'
export type TaskMode = 'lookup' | 'decide' | 'build' | 'reflect'
export type ThinkingMode = 'auto' | 'fast' | 'slow'
export type LoadBudget = 'light' | 'standard' | 'deep'
export type MemoryConsumer = 'agent' | 'human' | 'ui'

export type SortField = 'created_at' | 'updated_at' | 'confidence'
export type SortOrder = 'asc' | 'desc'

export interface BlockFilters {
  blockType: BlockType | null
  tag: string | null
  sortField: SortField
  sortOrder: SortOrder
}

// ── Galaxy types ──

export type GalaxyLayer = 'blocks' | 'triples' | 'communities' | 'summaries'

export interface GalaxyNode {
  id: string
  label: string
  type: BlockType
  confidence: number
  layer: GalaxyLayer
  x?: number
  y?: number
  z?: number
  vx?: number
  vy?: number
  vz?: number
}

export interface GalaxyLink {
  source: string
  target: string
  strength: number
}

export const BLOCK_TYPE_CONFIG: Record<BlockType, { label: string; color: string }> = {
  knowledge: { label: '知識', color: 'var(--blue)' },
  skill: { label: '技能', color: 'var(--green)' },
  attitude: { label: '態度', color: 'var(--mauve)' },
  general: { label: '通用', color: 'var(--text)' },
}

export const KG_LAYER_CONFIG: Record<GalaxyLayer, { label: string; color: string }> = {
  blocks: { label: '區塊', color: 'var(--text)' },
  triples: { label: '三元組', color: 'var(--teal)' },
  communities: { label: '社群', color: 'var(--blue)' },
  summaries: { label: '摘要', color: 'var(--peach)' },
}

// ── KG API types (mirrors kg_schemas.py) ──

export type BrowserTab = 'fast' | 'working' | 'deep' | 'skills'

export interface MemoryQueryOptions {
  taskMode: TaskMode
  thinkingMode: ThinkingMode
  loadBudget: LoadBudget
  consumer: MemoryConsumer
  topK: number
}

export interface MemoryEvidenceRef {
  kind: string
  ref_id: string
  title: string
  snippet: string | null
  score?: number | null
}

export interface MemoryCardRecord {
  id: string
  title: string
  summary: string
  why_relevant: string
  use_now: string
  layer: 'fast' | 'working' | 'deep'
  source_type: string
  confidence: number
  freshness: string | null
  tags: string[]
  evidence_refs: MemoryEvidenceRef[]
  source?: string | null  // "speculative_prefetch" for predicted hits
}

export interface MemoryQueryStrategy {
  task_mode: TaskMode
  thinking_mode_requested: ThinkingMode
  thinking_mode_used: 'fast' | 'slow'
  load_budget: LoadBudget
  consumer: MemoryConsumer
}

export interface MemoryQueryResponse {
  query: string
  strategy: MemoryQueryStrategy
  fast_cards: MemoryCardRecord[]
  working_cards: MemoryCardRecord[]
  deep_cards: MemoryCardRecord[]
  highlights: string[]
  metadata?: Record<string, unknown> | null
}

export interface MemoryInspectResponse {
  query: string
  strategy: MemoryQueryStrategy
  cards: MemoryCardRecord[]
  raw_sections: Record<string, MemoryEvidenceRef[]>
  metadata?: Record<string, unknown> | null
}

export interface Triple {
  id: string
  space_id: string
  created_by: string | null
  created_at: string
  updated_at: string
  subject: string
  predicate: string
  object: string
  source_session: string | null
  timestamp: string | null
  topic: string | null
  display_zh: string | null
}

export interface Community {
  id: string
  space_id: string
  name: string
  resolution_level: number
  size: number
  top_entities: string[]
  top_predicates: string[]
  summary: string | null
  description_zh: string | null
  parent_community_id: string | null
  modularity_score: number | null
  generation_batch: string | null
  created_at: string
  updated_at: string
}

export interface CommunityDetail extends Community {
  triples: Triple[]
  children: Community[]
}

export interface CommunitySummary {
  id: string
  space_id: string
  community_id: string
  summary: string
  key_findings: string[]
  representative_triples: string[]
  evidence_count: number | null
  tags: string[]
  llm_model: string | null
  created_at: string
  updated_at: string
}

export interface AttitudeFact {
  id: string
  space_id: string
  created_by: string | null
  created_at: string
  updated_at: string
  fact: string
  category: string
  operation: string
  confidence: number
  source_sessions: string[]
  superseded_by: string | null
  previous_version: string | null
}

export interface SkillProfile {
  id: string
  space_id: string
  created_at: string
  updated_at: string
  skill_name: string
  total_uses: number
  recent_uses: number
  success_rate: number
  avg_duration_ms: number | null
  auto_rate: number | null
  common_patterns: string[] | null
  learned_preferences: Record<string, unknown> | null
  pitfalls: Array<Record<string, unknown>> | null
  proficiency_level: string // "novice" | "proficient" | "expert"
  health_score: number | null
  evolution_notes: string[] | null
  last_synced_at: string | null
}

export interface CascadeRecallResult {
  summaries: CommunitySummary[]
  communities: Community[]
  triples: Triple[]
  blocks: any[]
  layers_searched: string[]
  routing_intent?: string
  routing_confidence?: number
  confidence_score?: number
  evaluation_verdict?: string
  evaluation_metadata?: Record<string, any>
}
