import { useMemo, useState } from 'react'
import MemoryLensCard from './MemoryLensCard'
import QueryControls from './QueryControls'
import SearchBar from './SearchBar'
import { useMemorySearch } from '../hooks/useMemorySearch'
import { useMemvaultStore } from '../stores'
import type { MemoryCardRecord, MemoryQueryOptions } from '../types'

const DEFAULT_QUERY_OPTIONS: MemoryQueryOptions = {
  taskMode: 'build',
  thinkingMode: 'auto',
  loadBudget: 'standard',
  consumer: 'human',
  topK: 6,
}

export default function RecallView() {
  const showAdvancedQuery = useMemvaultStore((s) => s.showAdvancedQuery)
  const toggleAdvancedQuery = useMemvaultStore((s) => s.toggleAdvancedQuery)
  const [queryOptions, setQueryOptions] = useState<MemoryQueryOptions>(DEFAULT_QUERY_OPTIONS)

  const { query, results, isSearching, setQuery, searchNow, clear } =
    useMemorySearch(queryOptions)

  const mergedCards = useMemo<MemoryCardRecord[]>(() => {
    if (!results) return []
    const all = [
      ...(results.fast_cards ?? []),
      ...(results.working_cards ?? []),
      ...(results.deep_cards ?? []),
    ]
    return all.sort((a, b) => b.confidence - a.confidence)
  }, [results])

  const totalCount = query.trim() ? mergedCards.length : undefined

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6 lg:py-6 space-y-4">
      <SearchBar
        value={query}
        onChange={setQuery}
        onSearch={searchNow}
        loading={isSearching}
        resultCount={totalCount}
        onClear={clear}
      />

      {/* Advanced query disclosure */}
      <div>
        <button
          onClick={toggleAdvancedQuery}
          className="flex items-center gap-1.5 text-xs transition-colors"
          style={{ color: 'var(--subtext1)', minHeight: 32 }}
        >
          <span
            className="inline-block transition-transform duration-200"
            style={{ transform: showAdvancedQuery ? 'rotate(0deg)' : 'rotate(-90deg)' }}
          >
            ▼
          </span>
          進階查詢
        </button>
        {showAdvancedQuery && (
          <div className="mt-2">
            <QueryControls
              options={queryOptions}
              onChange={(next) => setQueryOptions((prev) => ({ ...prev, ...next }))}
            />
          </div>
        )}
      </div>

      {/* Strategy summary */}
      {results?.strategy && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-xl px-4 py-2.5 text-xs"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--blue) 6%, var(--mantle))',
            color: 'var(--subtext0)',
          }}
        >
          <span>
            Thinking: <strong style={{ color: 'var(--text)' }}>{results.strategy.thinking_mode_used}</strong>
          </span>
          <span>
            Budget: <strong style={{ color: 'var(--text)' }}>{results.strategy.load_budget}</strong>
          </span>
          {results.highlights.length > 0 && (
            <span style={{ color: 'var(--blue)' }}>{results.highlights[0]}</span>
          )}
        </div>
      )}

      {/* Merged cards grid */}
      {mergedCards.length > 0 ? (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {mergedCards.map((card) => (
            <MemoryLensCard key={card.id} card={card} />
          ))}
        </div>
      ) : (
        <div
          className="rounded-2xl border px-5 py-16 text-center"
          style={{ borderColor: 'var(--surface0)', backgroundColor: 'var(--mantle)' }}
        >
          <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
            {query.trim()
              ? '沒有找到相關記憶。'
              : '輸入查詢，Recall 會整合 Fast / Working / Deep 三層記憶，按信心度排序呈現。'}
          </p>
        </div>
      )}
    </div>
  )
}
