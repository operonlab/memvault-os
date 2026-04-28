import { Layers, GitBranch, Network, Sparkles } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { memvaultApi } from '../../api'
import { kgApi } from '../../api/kg'
import { useCommunities, useProfile, useSummaries, useTriples } from '../../hooks/queries'
import UnderstandingGauge from '../UnderstandingGauge'
import EmptyState from '../shared/EmptyState'
import StatTile from '../shared/StatTile'

export default function DashboardPage() {
  const { data: profile, isLoading: profileLoading } = useProfile()
  const { data: syncStats } = useQuery({
    queryKey: ['memvault', 'syncStats'],
    queryFn: () => memvaultApi.syncStats(),
    staleTime: 5 * 60 * 1000,
  })
  const { data: triplesData } = useTriples(1)
  const { data: communities = [] } = useCommunities()
  const { data: summaries = [] } = useSummaries()

  const { data: recentBlocks } = useQuery({
    queryKey: ['memvault', 'blocks-preview'],
    queryFn: () => memvaultApi.listBlocks(1, 5, {}),
    staleTime: 5 * 60 * 1000,
  })
  const { data: recentTriples } = useQuery({
    queryKey: ['memvault', 'triples-preview'],
    queryFn: () => kgApi.listTriples(1, 5),
    staleTime: 5 * 60 * 1000,
  })

  const blockCount = syncStats?.synced ?? syncStats?.total ?? 0
  const tripleCount = triplesData?.total ?? 0
  const communityCount = communities.length
  const summaryCount = summaries.length

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6">
      {/* Header */}
      <div className="mb-5">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
          Knowledge Dashboard
        </h1>
        <p className="text-xs mt-1" style={{ color: 'var(--subtext0)' }}>
          知識圖譜總覽 — 區塊、三元組、社群、洞察
        </p>
      </div>

      {/* Stat Tiles */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-6">
        <NavLink to="/memvault/knowledge/blocks">
          <StatTile label="Blocks" value={blockCount} color="var(--peach)" icon={Layers} />
        </NavLink>
        <NavLink to="/memvault/knowledge/triples">
          <StatTile label="Triples" value={tripleCount} color="var(--blue)" icon={GitBranch} />
        </NavLink>
        <NavLink to="/memvault/knowledge/communities">
          <StatTile label="Communities" value={communityCount} color="var(--green)" icon={Network} />
        </NavLink>
        <NavLink to="/memvault/knowledge/insights">
          <StatTile label="Insights" value={summaryCount} color="var(--mauve)" icon={Sparkles} />
        </NavLink>
      </div>

      {/* KAS Gauge */}
      <div className="mb-6">
        <UnderstandingGauge profile={profile ?? null} loading={profileLoading} />
      </div>

      {/* Quick Previews */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Latest Blocks */}
        <PreviewSection title="Latest Blocks" color="var(--peach)" linkTo="/memvault/knowledge/blocks">
          {!recentBlocks ? (
            <EmptyState loading color="var(--peach)" />
          ) : recentBlocks.items.length === 0 ? (
            <EmptyState empty emptyTitle="尚無區塊" />
          ) : (
            <div className="space-y-2">
              {recentBlocks.items.map((b) => (
                <div
                  key={b.id}
                  className="rounded-lg border p-2.5"
                  style={{ backgroundColor: 'var(--base)', borderColor: 'var(--surface0)' }}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className="text-[10px] uppercase px-1.5 py-0.5 rounded"
                      style={{
                        backgroundColor: 'color-mix(in srgb, var(--peach) 15%, transparent)',
                        color: 'var(--peach)',
                      }}
                    >
                      {b.block_type}
                    </span>
                    {(b.tags ?? []).slice(0, 2).map((t: string) => (
                      <span key={t} className="text-[10px]" style={{ color: 'var(--subtext1)' }}>
                        #{t}
                      </span>
                    ))}
                  </div>
                  <p className="text-xs leading-relaxed line-clamp-2" style={{ color: 'var(--subtext0)' }}>
                    {b.content}
                  </p>
                </div>
              ))}
            </div>
          )}
        </PreviewSection>

        {/* Recent Triples */}
        <PreviewSection title="Recent Triples" color="var(--blue)" linkTo="/memvault/knowledge/triples">
          {!recentTriples ? (
            <EmptyState loading color="var(--blue)" />
          ) : recentTriples.items.length === 0 ? (
            <EmptyState empty emptyTitle="尚無三元組" />
          ) : (
            <div className="space-y-2">
              {recentTriples.items.map((t) => (
                <div
                  key={t.id}
                  className="rounded-lg border p-2.5"
                  style={{ backgroundColor: 'var(--base)', borderColor: 'var(--surface0)' }}
                >
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-xs font-medium" style={{ color: 'var(--teal)' }}>
                      {t.subject}
                    </span>
                    <span
                      className="px-1.5 py-0.5 rounded text-[10px]"
                      style={{
                        backgroundColor: 'color-mix(in srgb, var(--blue) 15%, transparent)',
                        color: 'var(--blue)',
                      }}
                    >
                      {t.predicate}
                    </span>
                    <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
                      {(t.object ?? '').length > 80 ? `${t.object.slice(0, 80)}…` : t.object}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </PreviewSection>

        {/* Top Communities */}
        <PreviewSection
          title="Top Communities"
          color="var(--green)"
          linkTo="/memvault/knowledge/communities"
          className="lg:col-span-2"
        >
          {communities.length === 0 ? (
            <EmptyState empty emptyTitle="尚無社群" />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {communities
                .slice()
                .sort((a, b) => b.size - a.size)
                .slice(0, 6)
                .map((c) => (
                  <div
                    key={c.id}
                    className="rounded-lg border p-2.5"
                    style={{ backgroundColor: 'var(--base)', borderColor: 'var(--surface0)' }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-block h-2 w-2 rounded-full shrink-0"
                        style={{ backgroundColor: 'var(--green)' }}
                      />
                      <span className="text-xs font-medium truncate" style={{ color: 'var(--green)' }}>
                        {c.name}
                      </span>
                      <span className="text-[10px] shrink-0" style={{ color: 'var(--subtext1)' }}>
                        {c.size}
                      </span>
                    </div>
                    {c.top_entities?.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {c.top_entities.slice(0, 3).map((e) => (
                          <span
                            key={e}
                            className="rounded px-1 py-0.5 text-[10px]"
                            style={{
                              backgroundColor: 'color-mix(in srgb, var(--green) 10%, transparent)',
                              color: 'var(--subtext0)',
                            }}
                          >
                            {e}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
            </div>
          )}
        </PreviewSection>
      </div>
    </div>
  )
}

function PreviewSection({
  title,
  color,
  linkTo,
  className = '',
  children,
}: {
  title: string
  color: string
  linkTo: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div
      className={`rounded-xl border p-4 ${className}`}
      style={{ backgroundColor: 'var(--mantle)', borderColor: 'var(--surface0)' }}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          {title}
        </h3>
        <NavLink
          to={linkTo}
          className="text-[11px] transition-colors"
          style={{ color }}
        >
          View All →
        </NavLink>
      </div>
      {children}
    </div>
  )
}
