import { useCallback, useState } from 'react'
import { useCascadeRecall } from '../hooks/queries'

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

const LAYER_CONFIG: Record<string, { label: string; color: string }> = {
  summaries: { label: '摘要 (L2)', color: 'var(--peach)' },
  communities: { label: '社群 (L1)', color: 'var(--blue)' },
  triples: { label: '三元組 (L0)', color: 'var(--teal)' },
  blocks: { label: '記憶區塊', color: 'var(--text)' },
}

function LayerSection({
  layerKey,
  items,
  expanded,
  onToggle,
}: {
  layerKey: string
  items: any[]
  expanded: boolean
  onToggle: () => void
}) {
  const config = LAYER_CONFIG[layerKey] ?? { label: layerKey, color: 'var(--text)' }
  if (items.length === 0) return null

  return (
    <div className="border-t pt-2" style={{ borderColor: 'var(--surface0)' }}>
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full text-left py-2"
        style={{ minHeight: 44 }}
      >
        <span
          className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
          style={{ backgroundColor: config.color }}
        />
        <span className="text-sm font-medium flex-1" style={{ color: 'var(--text)' }}>
          {config.label}
        </span>
        <span
          className="rounded-full px-2 py-0.5 text-xs font-medium"
          style={{
            backgroundColor: hexToRgba(config.color, 0.15),
            color: config.color,
          }}
        >
          {items.length}
        </span>
        <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
          {expanded ? '收合' : '展開'}
        </span>
      </button>

      {expanded && (
        <div className="mt-1 space-y-1.5 pl-4 sm:pl-5">
          {items.map((item: any, i: number) => (
            <div
              key={item.id ?? i}
              className="rounded-lg border px-3 py-2.5 text-sm"
              style={{
                backgroundColor: 'var(--mantle)',
                borderColor: 'var(--surface0)',
                color: 'var(--text)',
              }}
            >
              {layerKey === 'summaries' && item.summary}
              {layerKey === 'communities' && (
                <span>
                  <span className="font-medium">{item.name}</span>
                  <span className="ml-2 text-xs" style={{ color: 'var(--subtext0)' }}>
                    ({item.size} 成員)
                  </span>
                </span>
              )}
              {layerKey === 'triples' && (
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs">
                  <span style={{ color: 'var(--teal)' }}>{item.subject}</span>
                  <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                  <span style={{ color: 'var(--subtext1)' }}>{item.predicate}</span>
                  <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                  <span className="break-all">{item.object}</span>
                </div>
              )}
              {layerKey === 'blocks' && (
                <span className="text-sm line-clamp-2">{item.content?.slice(0, 80)}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function CascadeSearchBar() {
  const [query, setQuery] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const { data: result, isFetching } = useCascadeRecall(searchQuery)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    summaries: true,
    communities: true,
    triples: false,
    blocks: false,
  })

  const handleSearch = useCallback(() => {
    if (query.trim()) setSearchQuery(query.trim())
  }, [query])

  const handleClear = useCallback(() => {
    setQuery('')
    setSearchQuery('')
  }, [])

  const toggleLayer = (key: string) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))

  const totalHits = result
    ? result.summaries.length +
      result.communities.length +
      result.triples.length +
      result.blocks.length
    : 0

  return (
    <div>
      {/* Search input */}
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="跨層搜尋記憶..."
          className="flex-1 rounded-lg border px-3 py-2.5 text-sm outline-none transition-colors"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 44,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--peach)'
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--surface0)'
          }}
        />
        <button
          onClick={handleSearch}
          disabled={isFetching || !query.trim()}
          className="rounded-lg px-3 sm:px-4 py-2 text-sm font-medium transition-colors shrink-0"
          style={{
            backgroundColor: isFetching ? 'var(--surface0)' : 'var(--peach)',
            color: isFetching ? 'var(--subtext0)' : 'var(--base)',
            cursor: isFetching ? 'wait' : 'pointer',
            opacity: !query.trim() ? 0.5 : 1,
            minHeight: 44,
          }}
        >
          {isFetching ? '搜尋中...' : '跨層搜尋'}
        </button>
      </div>

      {/* Result summary */}
      {result && (
        <div className="mt-3">
          <div className="flex items-center justify-between mb-2 gap-2">
            <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
              找到 {totalHits} 筆結果（跨 {result.layers_searched.length} 層）
            </span>
            <button
              onClick={handleClear}
              className="text-xs py-1 px-2"
              style={{ color: 'var(--subtext0)', minHeight: 36 }}
            >
              清除
            </button>
          </div>

          <div className="space-y-1">
            {(['summaries', 'communities', 'triples', 'blocks'] as const).map((layer) => (
              <LayerSection
                key={layer}
                layerKey={layer}
                items={result[layer]}
                expanded={expanded[layer] ?? false}
                onToggle={() => toggleLayer(layer)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
