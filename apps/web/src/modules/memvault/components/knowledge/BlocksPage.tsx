import { LayoutGrid, List } from 'lucide-react'
import { useBlocks } from '../../hooks/queries'
import { useDeleteBlock } from '../../hooks/mutations'
import { useMemvaultStore } from '../../stores'
import type { BlockType } from '../../types'
import MemoryCard from '../MemoryCard'
import EmptyState from '../shared/EmptyState'
import Pagination from '../shared/Pagination'

const BLOCK_TYPES: { value: BlockType | null; label: string }[] = [
  { value: null, label: 'All' },
  { value: 'knowledge', label: '知識' },
  { value: 'attitude', label: '態度' },
  { value: 'skill', label: '技能' },
  { value: 'general', label: '通用' },
]

export default function BlocksPage() {
  const page = useMemvaultStore((s) => s.page)
  const pageSize = useMemvaultStore((s) => s.pageSize)
  const filters = useMemvaultStore((s) => s.filters)
  const viewMode = useMemvaultStore((s) => s.viewMode)
  const setPage = useMemvaultStore((s) => s.setPage)
  const setFilters = useMemvaultStore((s) => s.setFilters)
  const setViewMode = useMemvaultStore((s) => s.setViewMode)

  const { data, isLoading, isError } = useBlocks(page, pageSize, filters)
  const deleteBlock = useDeleteBlock()

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            Memory Blocks
          </h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--subtext0)' }}>
            從 session 萃取的記憶區塊 — {data?.total?.toLocaleString() ?? '...'} 筆
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setViewMode('grid')}
            className="rounded-lg p-2 transition-colors"
            style={{
              backgroundColor: viewMode === 'grid' ? 'var(--surface0)' : 'transparent',
              color: viewMode === 'grid' ? 'var(--text)' : 'var(--subtext1)',
            }}
          >
            <LayoutGrid size={16} />
          </button>
          <button
            type="button"
            onClick={() => setViewMode('list')}
            className="rounded-lg p-2 transition-colors"
            style={{
              backgroundColor: viewMode === 'list' ? 'var(--surface0)' : 'transparent',
              color: viewMode === 'list' ? 'var(--text)' : 'var(--subtext1)',
            }}
          >
            <List size={16} />
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {BLOCK_TYPES.map((bt) => (
          <button
            key={bt.value ?? 'all'}
            type="button"
            onClick={() => setFilters({ blockType: bt.value })}
            className="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
            style={{
              backgroundColor:
                filters.blockType === bt.value
                  ? 'color-mix(in srgb, var(--peach) 18%, var(--surface0))'
                  : 'var(--surface0)',
              color: filters.blockType === bt.value ? 'var(--peach)' : 'var(--subtext1)',
            }}
          >
            {bt.label}
          </button>
        ))}
        <input
          type="text"
          placeholder="Tag 篩選…"
          value={filters.tag ?? ''}
          onChange={(e) => setFilters({ tag: e.target.value || null })}
          className="rounded-lg border px-3 py-1.5 text-xs"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 32,
          }}
        />
      </div>

      {/* Content */}
      {isLoading ? (
        <EmptyState loading color="var(--peach)" />
      ) : isError ? (
        <EmptyState error="無法載入區塊資料" />
      ) : !data || data.items.length === 0 ? (
        <EmptyState empty emptyTitle="無符合條件的區塊" emptySubtitle="嘗試調整篩選條件" />
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {data.items.map((block) => (
            <MemoryCard
              key={block.id}
              block={block}
              onDelete={(id) => deleteBlock.mutate(id)}
            />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {data.items.map((block) => (
            <MemoryCard
              key={block.id}
              block={block}
              compact
              onDelete={(id) => deleteBlock.mutate(id)}
            />
          ))}
        </div>
      )}

      <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
    </div>
  )
}
