import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCommunities, useSummariesFiltered } from '../../hooks/queries'
import type { Community, CommunitySummary } from '../../types'
import EmptyState from '../shared/EmptyState'

export default function InsightsPage() {
  const [tag, setTag] = useState('')
  const [resolutionLevel, setResolutionLevel] = useState<number | undefined>(undefined)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data: summaries = [], isLoading, isError } = useSummariesFiltered(
    resolutionLevel,
    tag || undefined,
  )
  const { data: communities = [] } = useCommunities()
  const communityMap = new Map(communities.map((c) => [c.id, c]))

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
          Community Insights
          <span className="text-xs font-normal ml-2" style={{ color: 'var(--mauve)' }}>L2</span>
        </h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--subtext0)' }}>
          LLM 自動生成的社群摘要與關鍵發現 — {summaries.length.toLocaleString()} 筆
        </p>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <input
          type="text"
          placeholder="Tag 篩選…"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          className="rounded-lg border px-3 py-1.5 text-xs"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 32,
          }}
        />
        <select
          value={resolutionLevel ?? ''}
          onChange={(e) => setResolutionLevel(e.target.value ? Number(e.target.value) : undefined)}
          className="rounded-lg border px-3 py-1.5 text-xs"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 32,
          }}
        >
          <option value="">All Levels</option>
          <option value="0">Level 0 (Fine)</option>
          <option value="1">Level 1 (Medium)</option>
          <option value="2">Level 2 (Coarse)</option>
        </select>
      </div>

      {/* Content */}
      {isLoading ? (
        <EmptyState loading color="var(--mauve)" />
      ) : isError ? (
        <EmptyState error="無法載入洞察資料" />
      ) : summaries.length === 0 ? (
        <EmptyState empty emptyTitle="無符合條件的洞察" emptySubtitle="嘗試調整篩選條件" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {summaries.map((s) => (
            <SummaryCard
              key={s.id}
              summary={s}
              community={communityMap.get(s.community_id)}
              expanded={expandedId === s.id}
              onToggle={() => setExpandedId(expandedId === s.id ? null : s.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SummaryCard({
  summary,
  community,
  expanded,
  onToggle,
}: {
  summary: CommunitySummary
  community?: Community
  expanded: boolean
  onToggle: () => void
}) {
  const navigate = useNavigate()

  return (
    <div
      className="rounded-xl border p-4 transition-all duration-200 cursor-pointer"
      onClick={onToggle}
      style={{
        backgroundColor: expanded ? 'var(--mantle)' : 'var(--base)',
        borderColor: expanded ? 'var(--mauve)' : 'var(--surface0)',
      }}
    >
      {/* Community badge */}
      {community && (
        <div className="flex items-center gap-2 mb-2">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              navigate(`/memvault/knowledge/communities?highlight=${community.id}`)
            }}
            className="flex items-center gap-1.5 transition-colors"
          >
            <span
              className="inline-block h-2 w-2 rounded-full shrink-0"
              style={{ backgroundColor: 'var(--green)' }}
            />
            <span className="text-xs font-medium" style={{ color: 'var(--green)' }}>
              {community.name}
            </span>
          </button>
          {community.size > 0 && (
            <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
              {community.size} entities
            </span>
          )}
          {summary.evidence_count != null && (
            <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
              · {summary.evidence_count} evidence
            </span>
          )}
        </div>
      )}

      {/* Summary */}
      <p
        className={`text-sm leading-relaxed ${expanded ? '' : 'line-clamp-3'}`}
        style={{ color: 'var(--text)' }}
      >
        {summary.summary}
      </p>

      {/* Tags */}
      {summary.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {summary.tags.slice(0, expanded ? undefined : 5).map((t) => (
            <span
              key={t}
              className="rounded px-2 py-0.5 text-[11px]"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--teal) 12%, transparent)',
                color: 'var(--teal)',
              }}
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* LLM model badge */}
      {summary.llm_model && (
        <div className="mt-2">
          <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
            {summary.llm_model}
          </span>
        </div>
      )}

      {/* Expanded: Key Findings */}
      {expanded && summary.key_findings.length > 0 && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--surface0)' }}>
          <p
            className="text-[11px] uppercase tracking-[0.14em] mb-2"
            style={{ color: 'var(--subtext1)' }}
          >
            Key Findings
          </p>
          <ul className="space-y-1.5">
            {summary.key_findings.map((finding, i) => (
              <li
                key={i}
                className="text-xs leading-relaxed flex gap-2"
                style={{ color: 'var(--subtext0)' }}
              >
                <span className="shrink-0" style={{ color: 'var(--mauve)' }}>
                  •
                </span>
                {finding}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Expanded: Representative Triples */}
      {expanded && summary.representative_triples.length > 0 && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--surface0)' }}>
          <p
            className="text-[11px] uppercase tracking-[0.14em] mb-2"
            style={{ color: 'var(--subtext1)' }}
          >
            Representative Triples
          </p>
          <ul className="space-y-1">
            {summary.representative_triples.map((rt, i) => (
              <li
                key={i}
                className="text-[11px] leading-relaxed"
                style={{ color: 'var(--subtext0)' }}
              >
                {rt}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
