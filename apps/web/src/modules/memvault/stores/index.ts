import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { withJournal } from '@/shared/utils/journalMiddleware'
import type { MemoryBlock } from '@/types'
import type { BlockFilters, BrowserTab, GalaxyLayer, ViewMode } from '../types'

const DEFAULT_FILTERS: BlockFilters = {
  blockType: null,
  tag: null,
  sortField: 'updated_at',
  sortOrder: 'desc',
}

interface MemvaultState {
  viewMode: ViewMode
  filters: BlockFilters
  page: number
  pageSize: number
  selectedBlock: MemoryBlock | null
  searchQuery: string
  kg_activeTab: BrowserTab
  kg_galaxyLayers: Set<GalaxyLayer>

  selectBlock: (block: MemoryBlock | null) => void
  setPage: (page: number) => void
  setFilters: (filters: Partial<BlockFilters>) => void
  setViewMode: (mode: ViewMode) => void
  setSearchQuery: (query: string) => void
  clearSearch: () => void
  setKgActiveTab: (tab: BrowserTab) => void
  setKgGalaxyLayers: (layers: Set<GalaxyLayer>) => void
}

export const useMemvaultStore = create<MemvaultState>()(
  devtools(
    withJournal((set) => ({
      viewMode: 'grid',
      filters: DEFAULT_FILTERS,
      page: 1,
      pageSize: 20,
      selectedBlock: null,
      searchQuery: '',
      kg_activeTab: 'fast',
      kg_galaxyLayers: new Set<GalaxyLayer>(['blocks', 'summaries', 'communities']),

      selectBlock: (block) => set({ selectedBlock: block }, false, 'memvault/selectBlock'),
      setPage: (page) => set({ page }, false, 'memvault/setPage'),
      setFilters: (filters) =>
        set(
          (state) => ({ filters: { ...state.filters, ...filters }, page: 1 }),
          false,
          'memvault/setFilters',
        ),
      setViewMode: (mode) => set({ viewMode: mode }, false, 'memvault/setViewMode'),
      setSearchQuery: (query) => set({ searchQuery: query }, false, 'memvault/setSearchQuery'),
      clearSearch: () => set({ searchQuery: '' }, false, 'memvault/clearSearch'),
      setKgActiveTab: (tab) => set({ kg_activeTab: tab }, false, 'memvault/setKgActiveTab'),
      setKgGalaxyLayers: (layers) =>
        set({ kg_galaxyLayers: layers }, false, 'memvault/setKgGalaxyLayers'),
    })),
    { name: 'memvaultStore' },
  ),
)
