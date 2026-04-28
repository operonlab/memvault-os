import { useCallback, useRef, useState } from 'react'
import { relativeTime } from '../../../shared/utils/time'
import { useDeleteTriple } from '../hooks/mutations'
import { useCommunities, useCommunityDetail, useSummaries, useTriples } from '../hooks/queries'
import type { Community, CommunityDetail, CommunitySummary } from '../types'
import InfoTip from './InfoTip'

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

// ── Layer Section Header ──

function LayerHeader({
  color,
  label,
  count,
  countUnit,
  collapsed,
  onToggle,
  info,
}: {
  color: string
  label: string
  count: number
  countUnit: string
  collapsed: boolean
  onToggle: () => void
  info?: string
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 text-left flex-1"
        style={{ minHeight: 44 }}
      >
        <span
          className="text-xs transition-transform duration-200"
          style={{
            color,
            display: 'inline-block',
            transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
          }}
        >
          ▼
        </span>
        <span
          className="inline-block h-3 w-3 rounded-full shrink-0"
          style={{ backgroundColor: color }}
        />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          {label}
        </h3>
      </button>
      {info && <InfoTip text={info} />}
      <span className="text-xs shrink-0" style={{ color: 'var(--subtext0)' }}>
        {count} {countUnit}
      </span>
    </div>
  )
}

// ── Community Summary Card (L2) ──

function CommunitySummaryCard({
  summary,
  onExpand,
  expanded,
  relatedCommunity,
  onCommunityNav,
}: {
  summary: CommunitySummary
  onExpand: () => void
  expanded: boolean
  relatedCommunity: Community | undefined
  onCommunityNav?: (communityId: string) => void
}) {
  return (
    <div
      className="rounded-xl border p-4 cursor-pointer transition-all duration-200"
      style={{
        backgroundColor: hexToRgba('var(--peach)', 0.06),
        borderColor: expanded ? 'var(--peach)' : 'var(--surface0)',
      }}
      onClick={onExpand}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'var(--peach)'
      }}
      onMouseLeave={(e) => {
        if (!expanded) e.currentTarget.style.borderColor = 'var(--surface0)'
      }}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <span className="text-xs shrink-0" style={{ color: 'var(--subtext0)' }}>
          {summary.evidence_count ?? 0} 證據
        </span>
        {summary.llm_model && (
          <span
            className="rounded px-1.5 py-0.5 text-xs"
            style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
          >
            {summary.llm_model}
          </span>
        )}
      </div>

      <p className="text-sm leading-relaxed mb-2" style={{ color: 'var(--text)' }}>
        {summary.summary}
      </p>

      {summary.tags.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {summary.tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="rounded px-1.5 py-0.5 text-xs"
              style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Expanded: show key findings + related community */}
      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: 'var(--surface0)' }}>
          {summary.key_findings.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                主要發現
              </p>
              <ul className="space-y-1">
                {summary.key_findings.map((finding, i) => (
                  <li key={i} className="text-xs leading-relaxed" style={{ color: 'var(--text)' }}>
                    • {finding}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {relatedCommunity && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                所屬社群
              </p>
              <div
                className="flex items-center justify-between text-xs px-2 py-2 rounded cursor-pointer transition-colors gap-2"
                style={{ backgroundColor: hexToRgba('var(--blue)', 0.08) }}
                onClick={(e) => {
                  e.stopPropagation()
                  onCommunityNav?.(relatedCommunity.id)
                }}
                title="點擊跳轉到此社群"
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <span
                    className="inline-block h-2 w-2 rounded-full shrink-0"
                    style={{ backgroundColor: 'var(--blue)' }}
                  />
                  <span
                    className="truncate"
                    style={{
                      color: 'var(--blue)',
                      textDecoration: 'underline',
                      textDecorationStyle: 'dotted',
                    }}
                  >
                    {relatedCommunity.name}
                  </span>
                </div>
                <span style={{ color: 'var(--subtext0)' }}>{relatedCommunity.size} 成員</span>
              </div>
            </div>
          )}

          <p className="text-xs" style={{ color: 'var(--subtext0)' }}>
            {relativeTime(summary.created_at)}
          </p>
        </div>
      )}
    </div>
  )
}

// ── Community Card (L1) ──

function CommunityCard({
  community,
  onExpand,
  expanded,
  detail,
}: {
  community: Community
  onExpand: () => void
  expanded: boolean
  detail: CommunityDetail | null
}) {
  const descZh = community.description_zh
  return (
    <div
      className="rounded-xl border p-4 cursor-pointer transition-all duration-200"
      style={{
        backgroundColor: hexToRgba('var(--blue)', 0.06),
        borderColor: expanded ? 'var(--blue)' : 'var(--surface0)',
      }}
      onClick={onExpand}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'var(--blue)'
      }}
      onMouseLeave={(e) => {
        if (!expanded) e.currentTarget.style.borderColor = 'var(--surface0)'
      }}
    >
      <div className="flex items-start sm:items-center justify-between mb-2 gap-2">
        <span className="text-sm font-medium flex-1 min-w-0" style={{ color: 'var(--text)' }}>
          {community.name}
        </span>
        <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
          <span
            className="rounded-full px-2 py-0.5 text-xs"
            style={{
              backgroundColor: hexToRgba('var(--blue)', 0.15),
              color: 'var(--blue)',
            }}
          >
            {community.size} 成員
          </span>
          {community.resolution_level !== undefined && (
            <span
              className="rounded px-1.5 py-0.5 text-xs"
              style={{
                backgroundColor: hexToRgba('var(--mauve)', 0.15),
                color: 'var(--mauve)',
              }}
            >
              L{community.resolution_level}
            </span>
          )}
        </div>
      </div>

      {/* Collapsed: truncated preview */}
      {!expanded && (descZh || community.summary) && (
        <p className="text-xs mb-2 line-clamp-2" style={{ color: 'var(--subtext1)' }}>
          {descZh || community.summary}
        </p>
      )}

      {!expanded && (
        <div className="flex gap-1 flex-wrap">
          {community.top_entities.slice(0, 4).map((e) => (
            <span
              key={e}
              className="rounded px-1.5 py-0.5 text-xs"
              style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
            >
              {e}
            </span>
          ))}
          {community.top_entities.length > 4 && (
            <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
              +{community.top_entities.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Expanded: full detail, no truncation */}
      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-3" style={{ borderColor: 'var(--surface0)' }}>
          {/* LLM 白話文摘要（完整） */}
          {descZh && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--peach)' }}>
                白話摘要
              </p>
              <p
                className="text-xs leading-relaxed whitespace-pre-wrap"
                style={{ color: 'var(--text)' }}
              >
                {descZh}
              </p>
            </div>
          )}

          {/* 規則式結構分析（完整） */}
          {community.summary && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                結構分析
              </p>
              <p
                className="text-xs leading-relaxed whitespace-pre-wrap"
                style={{ color: 'var(--overlay0)' }}
              >
                {community.summary}
              </p>
            </div>
          )}

          {/* 全部 Entities */}
          {community.top_entities.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                實體 ({community.top_entities.length})
              </p>
              <div className="flex gap-1 flex-wrap">
                {community.top_entities.map((e) => (
                  <span
                    key={e}
                    className="rounded px-1.5 py-0.5 text-xs"
                    style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
                  >
                    {e}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 全部 Predicates */}
          {community.top_predicates.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                常見關係 ({community.top_predicates.length})
              </p>
              <div className="flex gap-1 flex-wrap">
                {community.top_predicates.map((p) => (
                  <span
                    key={p}
                    className="rounded px-1.5 py-0.5 text-xs"
                    style={{
                      backgroundColor: hexToRgba('var(--mauve)', 0.12),
                      color: 'var(--mauve)',
                    }}
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Member triples (if populated) */}
          {detail && detail.triples.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                成員三元組
              </p>
              {detail.triples.slice(0, 20).map((t) => (
                <div
                  key={t.id}
                  className="text-xs px-2 py-1.5 rounded mb-1"
                  style={{ backgroundColor: 'var(--base)' }}
                >
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                    <span style={{ color: 'var(--teal)' }}>{t.subject}</span>
                    <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                    <span style={{ color: 'var(--subtext1)' }}>{t.predicate}</span>
                    <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                    <span className="break-all" style={{ color: 'var(--text)' }}>
                      {t.object}
                    </span>
                  </div>
                </div>
              ))}
              {detail.triples.length > 20 && (
                <p className="text-xs" style={{ color: 'var(--subtext0)' }}>
                  ...還有 {detail.triples.length - 20} 筆
                </p>
              )}
            </div>
          )}

          {/* Child communities (if populated) */}
          {detail && detail.children.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                子社群 ({detail.children.length})
              </p>
              <div className="flex gap-1 flex-wrap">
                {detail.children.map((child) => (
                  <span
                    key={child.id}
                    className="rounded px-1.5 py-0.5 text-xs"
                    style={{
                      backgroundColor: hexToRgba('var(--blue)', 0.1),
                      color: 'var(--blue)',
                    }}
                  >
                    {child.name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {community.modularity_score !== null && community.modularity_score !== undefined && (
            <p className="text-xs" style={{ color: 'var(--subtext0)' }}>
              模組化分數：{community.modularity_score.toFixed(4)}
            </p>
          )}

          {community.generation_batch && (
            <p className="text-xs" style={{ color: 'var(--subtext0)' }}>
              生成批次：{community.generation_batch}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Triple Table (L0) ──

function TripleTable() {
  const [page, setPage] = useState(1)
  const [filterPredicate, setFilterPredicate] = useState('')
  const { data: triplesData, isLoading } = useTriples(page)
  const deleteTripleMutation = useDeleteTriple()

  const triples = triplesData?.items ?? []
  const total = triplesData?.total ?? 0
  const totalPages = Math.ceil(total / 20)

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={filterPredicate}
          onChange={(e) => setFilterPredicate(e.target.value)}
          placeholder="篩選 predicate..."
          className="rounded-lg border px-2 py-2 text-xs outline-none flex-1"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 44,
          }}
        />
        <span className="text-xs shrink-0" style={{ color: 'var(--subtext0)' }}>
          共 {total} 筆
        </span>
      </div>

      {isLoading && triples.length === 0 ? (
        <div className="flex justify-center py-8">
          <div
            className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
            style={{ borderColor: 'var(--teal)', borderTopColor: 'transparent' }}
          />
        </div>
      ) : (
        <div className="space-y-1.5">
          {triples
            .filter(
              (t) =>
                !filterPredicate ||
                t.predicate.toLowerCase().includes(filterPredicate.toLowerCase()),
            )
            .map((t) => (
              <div
                key={t.id}
                className="group rounded-lg border px-3 py-2.5 text-xs"
                style={{
                  backgroundColor: 'var(--mantle)',
                  borderColor: 'var(--surface0)',
                }}
              >
                {/* Chinese display (if available) */}
                {t.display_zh && (
                  <p className="text-xs mb-1" style={{ color: 'var(--text)' }}>
                    {t.display_zh}
                  </p>
                )}
                {/* Triple SPO */}
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 mb-1.5">
                  <span className="font-medium" style={{ color: 'var(--teal)' }}>
                    {t.subject}
                  </span>
                  <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                  <span style={{ color: 'var(--mauve)' }}>{t.predicate}</span>
                  <span style={{ color: 'var(--subtext0)' }}>&rarr;</span>
                  <span className="break-all flex-1" style={{ color: 'var(--text)' }}>
                    {t.object}
                  </span>
                </div>
                {/* Footer row */}
                <div className="flex items-center justify-between gap-2">
                  {t.topic && (
                    <span
                      className="rounded px-1.5 py-0.5"
                      style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
                    >
                      {t.topic}
                    </span>
                  )}
                  <div className="flex-1" />
                  <button
                    onClick={() => {
                      if (confirm(`刪除三元組：${t.subject} → ${t.predicate} → ${t.object}？`))
                        deleteTripleMutation.mutate(t.id)
                    }}
                    className="rounded px-2 py-1 text-xs transition-opacity sm:opacity-0 sm:group-hover:opacity-100"
                    style={{ color: 'var(--red)', minHeight: 36 }}
                    title="刪除"
                  >
                    刪除
                  </button>
                </div>
              </div>
            ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4">
          <button
            onClick={() => setPage(page - 1)}
            disabled={page <= 1}
            className="rounded-lg px-3 py-2 text-xs transition-colors"
            style={{
              backgroundColor: 'var(--surface0)',
              color: page <= 1 ? 'var(--subtext0)' : 'var(--text)',
              opacity: page <= 1 ? 0.5 : 1,
              minHeight: 44,
            }}
          >
            上一頁
          </button>
          <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={page >= totalPages}
            className="rounded-lg px-3 py-2 text-xs transition-colors"
            style={{
              backgroundColor: 'var(--surface0)',
              color: page >= totalPages ? 'var(--subtext0)' : 'var(--text)',
              opacity: page >= totalPages ? 0.5 : 1,
              minHeight: 44,
            }}
          >
            下一頁
          </button>
        </div>
      )}
    </div>
  )
}

// ── Main KG Explorer Panel ──

export default function KgExplorerPanel() {
  const { data: summaries = [], isLoading: summariesLoading } = useSummaries()
  const { data: communities = [], isLoading: communitiesLoading } = useCommunities()

  const [expandedSummary, setExpandedSummary] = useState<string | null>(null)
  const [expandedCommunity, setExpandedCommunity] = useState<string | null>(null)
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set())

  const { data: selectedCommunityDetail } = useCommunityDetail(expandedCommunity)
  const { data: triplesData } = useTriples(1)
  const triplesTotal = triplesData?.total ?? 0

  const communitySectionRef = useRef<HTMLElement>(null)

  const handleCommunityExpand = (id: string) => {
    setExpandedCommunity(expandedCommunity === id ? null : id)
  }

  const toggleSection = useCallback((key: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const handleCommunityNav = useCallback((communityId: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev)
      next.delete('communities')
      return next
    })
    setExpandedCommunity(communityId)
    setTimeout(() => {
      communitySectionRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    }, 100)
  }, [])

  return (
    <div className="space-y-6">
      {/* L2: Community Summaries */}
      <section>
        <LayerHeader
          color="var(--peach)"
          label="社群摘要 (L2)"
          count={summaries.length}
          countUnit="條"
          collapsed={collapsedSections.has('summaries')}
          onToggle={() => toggleSection('summaries')}
          info="社群摘要是從 Leiden 社群自動生成的高層洞察。每條摘要描述一個知識社群的主要主題與關鍵發現，由 LLM 自動生成。"
        />

        {!collapsedSections.has('summaries') &&
          (summariesLoading && summaries.length === 0 ? (
            <div className="flex justify-center py-6">
              <div
                className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
                style={{ borderColor: 'var(--peach)', borderTopColor: 'transparent' }}
              />
            </div>
          ) : summaries.length === 0 ? (
            <p className="text-sm py-4" style={{ color: 'var(--subtext0)' }}>
              尚未產生社群摘要
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {summaries.map((s) => (
                <CommunitySummaryCard
                  key={s.id}
                  summary={s}
                  expanded={expandedSummary === s.id}
                  onExpand={() => setExpandedSummary(expandedSummary === s.id ? null : s.id)}
                  relatedCommunity={communities.find((c) => c.id === s.community_id)}
                  onCommunityNav={handleCommunityNav}
                />
              ))}
            </div>
          ))}
      </section>

      {/* L1: Communities */}
      <section ref={communitySectionRef}>
        <LayerHeader
          color="var(--blue)"
          label="知識社群 (L1)"
          count={communities.length}
          countUnit="個"
          collapsed={collapsedSections.has('communities')}
          onToggle={() => toggleSection('communities')}
          info="知識社群是由 Leiden 演算法從三元組圖譜中自動偵測的主題群組。每個社群包含語意相近的知識節點，並附有摘要和層級資訊。"
        />

        {!collapsedSections.has('communities') &&
          (communities.length === 0 ? (
            <p className="text-sm py-4" style={{ color: 'var(--subtext0)' }}>
              尚未產生知識社群
            </p>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {communities.map((c) => (
                <CommunityCard
                  key={c.id}
                  community={c}
                  expanded={expandedCommunity === c.id}
                  onExpand={() => handleCommunityExpand(c.id)}
                  detail={expandedCommunity === c.id ? selectedCommunityDetail ?? null : null}
                />
              ))}
            </div>
          ))}
      </section>

      {/* L0: Triples */}
      <section>
        <LayerHeader
          color="var(--teal)"
          label="知識三元組 (L0)"
          count={triplesTotal}
          countUnit="筆"
          collapsed={collapsedSections.has('triples')}
          onToggle={() => toggleSection('triples')}
          info="三元組是知識圖譜的最小單位，格式為「主詞 → 關係 → 受詞」。每條三元組記錄一個具體的知識事實，由對話中自動提取。這些是所有上層結構（社群、摘要）的基礎資料。"
        />

        {!collapsedSections.has('triples') && <TripleTable />}
      </section>
    </div>
  )
}
