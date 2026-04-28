import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCommunities, useCommunityDetail } from '../../hooks/queries'
import type { Community } from '../../types'
import EmptyState from '../shared/EmptyState'
import Pagination from '../shared/Pagination'

const RESOLUTION_LEVELS = [
  { value: undefined, label: 'All' },
  { value: 0, label: 'Level 0 (Fine)' },
  { value: 1, label: 'Level 1 (Medium)' },
  { value: 2, label: 'Level 2 (Coarse)' },
] as const

const PAGE_SIZE = 20

export default function CommunitiesPage() {
  const [searchParams] = useSearchParams()
  const highlightId = searchParams.get('highlight')

  const [resolutionFilter, setResolutionFilter] = useState<number | undefined>(undefined)
  const [expandedId, setExpandedId] = useState<string | null>(highlightId)
  const [page, setPage] = useState(1)

  const { data: communities = [], isLoading, isError } = useCommunities()

  const filtered = useMemo(() => {
    let list = communities
    if (resolutionFilter !== undefined) {
      list = list.filter((c) => c.resolution_level === resolutionFilter)
    }
    return list.sort((a, b) => b.size - a.size)
  }, [communities, resolutionFilter])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
          Knowledge Communities
          <span className="text-xs font-normal ml-2" style={{ color: 'var(--green)' }}>L1</span>
        </h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--subtext0)' }}>
          Leiden 演算法偵測的知識主題群組 — {filtered.length.toLocaleString()} 個
        </p>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        {RESOLUTION_LEVELS.map((rl) => (
          <button
            key={rl.label}
            type="button"
            onClick={() => { setResolutionFilter(rl.value); setPage(1) }}
            className="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
            style={{
              backgroundColor:
                resolutionFilter === rl.value
                  ? 'color-mix(in srgb, var(--green) 18%, var(--surface0))'
                  : 'var(--surface0)',
              color: resolutionFilter === rl.value ? 'var(--green)' : 'var(--subtext1)',
            }}
          >
            {rl.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading ? (
        <EmptyState loading color="var(--green)" />
      ) : isError ? (
        <EmptyState error="無法載入社群資料" />
      ) : paged.length === 0 ? (
        <EmptyState empty emptyTitle="無符合條件的社群" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {paged.map((c) => (
            <CommunityCard
              key={c.id}
              community={c}
              expanded={expandedId === c.id}
              onToggle={() => setExpandedId(expandedId === c.id ? null : c.id)}
            />
          ))}
        </div>
      )}

      <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
    </div>
  )
}

function CommunityCard({
  community: c,
  expanded,
  onToggle,
}: {
  community: Community
  expanded: boolean
  onToggle: () => void
}) {
  const { data: detail } = useCommunityDetail(expanded ? c.id : null)
  const navigate = useNavigate()

  return (
    <div
      className="rounded-xl border p-4 transition-all duration-200 cursor-pointer"
      onClick={onToggle}
      style={{
        backgroundColor: expanded ? 'var(--mantle)' : 'var(--base)',
        borderColor: expanded ? 'var(--green)' : 'var(--surface0)',
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="inline-block h-2.5 w-2.5 rounded-full shrink-0"
          style={{ backgroundColor: 'var(--green)' }}
        />
        <span className="text-sm font-medium" style={{ color: 'var(--green)' }}>
          {c.name}
        </span>
        <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
          {c.size} entities
        </span>
        <span
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--lavender) 12%, transparent)',
            color: 'var(--lavender)',
          }}
        >
          L{c.resolution_level}
        </span>
        {c.modularity_score != null && (
          <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
            mod {c.modularity_score.toFixed(3)}
          </span>
        )}
      </div>

      {/* Top Entities Preview (collapsed) */}
      {!expanded && c.top_entities?.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {c.top_entities.slice(0, 4).map((e) => (
            <span
              key={e}
              className="rounded px-1.5 py-0.5 text-[10px]"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--green) 10%, transparent)',
                color: 'var(--subtext0)',
              }}
            >
              {e}
            </span>
          ))}
          {c.top_entities.length > 4 && (
            <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
              +{c.top_entities.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Expanded Detail */}
      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-3" style={{ borderColor: 'var(--surface0)' }}>
          {c.description_zh && (
            <p className="text-xs leading-relaxed" style={{ color: 'var(--subtext0)' }}>
              {c.description_zh}
            </p>
          )}
          {c.summary && !c.description_zh && (
            <p className="text-xs leading-relaxed" style={{ color: 'var(--subtext0)' }}>
              {c.summary}
            </p>
          )}

          {/* All Top Entities */}
          {c.top_entities?.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--subtext1)' }}>
                Top Entities
              </p>
              <div className="flex flex-wrap gap-1">
                {c.top_entities.map((e) => (
                  <span
                    key={e}
                    className="rounded px-1.5 py-0.5 text-[10px]"
                    style={{
                      backgroundColor: 'color-mix(in srgb, var(--green) 12%, transparent)',
                      color: 'var(--green)',
                    }}
                  >
                    {e}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Top Predicates */}
          {c.top_predicates?.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--subtext1)' }}>
                Top Predicates
              </p>
              <div className="flex flex-wrap gap-1">
                {c.top_predicates.map((p) => (
                  <span
                    key={p}
                    className="rounded px-1.5 py-0.5 text-[10px]"
                    style={{
                      backgroundColor: 'color-mix(in srgb, var(--blue) 12%, transparent)',
                      color: 'var(--blue)',
                    }}
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Member Triples (from detail query) */}
          {detail?.triples && detail.triples.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--subtext1)' }}>
                Member Triples ({detail.triples.length})
              </p>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {detail.triples.slice(0, 10).map((t) => (
                  <div
                    key={t.id}
                    className="rounded border p-2 text-[11px]"
                    style={{ backgroundColor: 'var(--base)', borderColor: 'var(--surface0)' }}
                  >
                    <span style={{ color: 'var(--teal)' }}>{t.subject}</span>
                    {' '}
                    <span style={{ color: 'var(--blue)' }}>{t.predicate}</span>
                    {' '}
                    <span style={{ color: 'var(--subtext0)' }}>{t.object}</span>
                  </div>
                ))}
                {detail.triples.length > 10 && (
                  <p className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
                    … and {detail.triples.length - 10} more
                  </p>
                )}
              </div>
            </div>
          )}

          {/* Children */}
          {detail?.children && detail.children.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider mb-1.5" style={{ color: 'var(--subtext1)' }}>
                Child Communities ({detail.children.length})
              </p>
              <div className="flex flex-wrap gap-1.5">
                {detail.children.map((child) => (
                  <span
                    key={child.id}
                    className="rounded px-2 py-0.5 text-[10px]"
                    style={{
                      backgroundColor: 'color-mix(in srgb, var(--green) 10%, transparent)',
                      color: 'var(--green)',
                    }}
                  >
                    {child.name} ({child.size})
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Cross-layer nav */}
          {c.top_entities?.[0] && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                navigate(`/memvault/knowledge/triples?subject=${encodeURIComponent(c.top_entities[0])}`)
              }}
              className="text-[11px] transition-colors"
              style={{ color: 'var(--blue)' }}
            >
              查看相關 Triples →
            </button>
          )}
        </div>
      )}
    </div>
  )
}
