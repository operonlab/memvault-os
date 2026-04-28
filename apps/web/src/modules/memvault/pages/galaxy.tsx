import { lazy, Suspense, useCallback, useState } from 'react'
import type { MemoryBlock } from '@/types'
import { relativeTime } from '../../../shared/utils/time'
const GalaxyCanvas = lazy(() => import('../components/GalaxyCanvas'))
import LayerToggle from '../components/LayerToggle'
import { useBlocks, useCommunities, useSummaries, useTriples } from '../hooks/queries'
import { useGalaxy } from '../hooks/useGalaxy'
import { useMemvaultStore } from '../stores'
import type { GalaxyNode } from '../types'
import { BLOCK_TYPE_CONFIG } from '../types'

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

function BlockDetailPanel({ block, onClose }: { block: MemoryBlock; onClose: () => void }) {
  const config = BLOCK_TYPE_CONFIG[block.block_type] ?? BLOCK_TYPE_CONFIG.general
  const confidencePct = `${Math.round(block.confidence * 100)}%`

  return (
    <>
      {/* Mobile: bottom sheet overlay */}
      <div
        className="lg:hidden fixed inset-0 z-40"
        style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
        onClick={onClose}
      />
      <div
        className="lg:hidden fixed bottom-0 left-0 right-0 z-50 rounded-t-2xl border-t overflow-hidden max-h-[60vh] flex flex-col"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'var(--surface0)' }}
        >
          <div className="flex items-center gap-2">
            <span
              className="rounded-full px-2.5 py-0.5 text-xs font-medium"
              style={{
                backgroundColor: hexToRgba(config.color, 0.18),
                color: config.color,
                border: `1px solid ${config.color}`,
              }}
            >
              {config.label}
            </span>
            <span className="text-sm font-semibold" style={{ color: config.color }}>
              {confidencePct}
            </span>
          </div>
          <button
            onClick={onClose}
            className="flex items-center justify-center rounded-lg text-xs transition-colors"
            style={{ color: 'var(--subtext0)', minWidth: 44, minHeight: 44 }}
          >
            關閉
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          <p
            className="text-sm leading-relaxed whitespace-pre-wrap"
            style={{ color: 'var(--text)' }}
          >
            {block.content}
          </p>

          {block.tags.length > 0 && (
            <div>
              <h4
                className="text-xs font-medium mb-2 uppercase tracking-wider"
                style={{ color: 'var(--subtext0)' }}
              >
                標籤
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {block.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded px-2 py-0.5 text-xs"
                    style={{
                      backgroundColor: 'var(--surface0)',
                      color: 'var(--subtext0)',
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div
            className="space-y-1.5 pt-2 border-t text-xs"
            style={{ borderColor: 'var(--surface0)', color: 'var(--subtext1)' }}
          >
            {block.source_session && (
              <p>
                <span style={{ color: 'var(--subtext0)' }}>來源：</span>
                {block.source_session.slice(0, 8)}...
              </p>
            )}
            <p>
              <span style={{ color: 'var(--subtext0)' }}>建立：</span>
              {relativeTime(block.created_at)}
            </p>
            <p>
              <span style={{ color: 'var(--subtext0)' }}>更新：</span>
              {relativeTime(block.updated_at)}
            </p>
          </div>
        </div>
      </div>

      {/* Desktop: side panel */}
      <div
        className="hidden lg:flex flex-col h-full border-l overflow-hidden"
        style={{
          width: 360,
          minWidth: 360,
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'var(--surface0)' }}
        >
          <div className="flex items-center gap-2">
            <span
              className="rounded-full px-2.5 py-0.5 text-xs font-medium"
              style={{
                backgroundColor: hexToRgba(config.color, 0.18),
                color: config.color,
                border: `1px solid ${config.color}`,
              }}
            >
              {config.label}
            </span>
            <span className="text-sm font-semibold" style={{ color: config.color }}>
              {confidencePct}
            </span>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-xs transition-colors"
            style={{ color: 'var(--subtext0)' }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--surface0)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
            }}
          >
            關閉
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          <div>
            <p
              className="text-sm leading-relaxed whitespace-pre-wrap"
              style={{ color: 'var(--text)' }}
            >
              {block.content}
            </p>
          </div>

          {block.tags.length > 0 && (
            <div>
              <h4
                className="text-xs font-medium mb-2 uppercase tracking-wider"
                style={{ color: 'var(--subtext0)' }}
              >
                標籤
              </h4>
              <div className="flex flex-wrap gap-1.5">
                {block.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded px-2 py-0.5 text-xs"
                    style={{
                      backgroundColor: 'var(--surface0)',
                      color: 'var(--subtext0)',
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div
            className="space-y-1.5 pt-2 border-t text-xs"
            style={{ borderColor: 'var(--surface0)', color: 'var(--subtext1)' }}
          >
            {block.source_session && (
              <p>
                <span style={{ color: 'var(--subtext0)' }}>來源：</span>
                {block.source_session.slice(0, 8)}...
              </p>
            )}
            <p>
              <span style={{ color: 'var(--subtext0)' }}>建立：</span>
              {relativeTime(block.created_at)}
            </p>
            <p>
              <span style={{ color: 'var(--subtext0)' }}>更新：</span>
              {relativeTime(block.updated_at)}
            </p>
          </div>
        </div>
      </div>
    </>
  )
}

export default function GalaxyPage() {
  const { page, pageSize, filters, selectedBlock, selectBlock, kg_galaxyLayers, setKgGalaxyLayers } =
    useMemvaultStore()

  const blocksQuery = useBlocks(page, pageSize, filters)
  const triplesQuery = useTriples(1)
  const communitiesQuery = useCommunities()
  const summariesQuery = useSummaries()

  const blocks = blocksQuery.data?.items ?? []
  const triples = triplesQuery.data?.items ?? []
  const communities = communitiesQuery.data ?? []
  const summaries = summariesQuery.data ?? []

  const [showLayerPanel, setShowLayerPanel] = useState(false)

  const { nodes, links } = useGalaxy({
    blocks,
    triples,
    communities,
    summaries,
    visibleLayers: kg_galaxyLayers,
  })

  const handleNodeClick = useCallback(
    (node: GalaxyNode) => {
      const block = blocks.find((b) => b.id === node.id)
      if (block) {
        if (selectedBlock?.id === block.id) {
          selectBlock(null)
        } else {
          selectBlock(block)
        }
      }
    },
    [blocks, selectBlock, selectedBlock],
  )

  const handleEmptyClick = useCallback(() => {
    selectBlock(null)
  }, [selectBlock])

  const isLoading = blocksQuery.isLoading

  return (
    <div className="flex flex-col h-full px-3 py-3 sm:px-4 sm:py-4 lg:p-6">
      {/* Header */}
      <div className="flex items-start sm:items-center justify-between mb-3 shrink-0 gap-2">
        <h1 className="text-lg sm:text-xl font-bold" style={{ color: 'var(--text)' }}>
          KAS 星系圖
        </h1>

        {/* Desktop: Layer Toggle + Legend */}
        <div className="hidden sm:flex items-center gap-3">
          <LayerToggle layers={kg_galaxyLayers} onChange={setKgGalaxyLayers} />
          <span className="mx-1 h-3 border-l" style={{ borderColor: 'var(--surface0)' }} />
          <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
            拖曳旋轉 | 滾輪縮放 | 點擊查看
          </span>
        </div>

        {/* Mobile: Layer toggle button */}
        <button
          onClick={() => setShowLayerPanel(!showLayerPanel)}
          className="sm:hidden flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors"
          style={{
            backgroundColor: showLayerPanel ? 'var(--surface0)' : 'var(--mantle)',
            color: 'var(--subtext0)',
            border: '1px solid var(--surface0)',
            minHeight: 44,
          }}
        >
          圖層設定
        </button>
      </div>

      {/* Mobile: Layer toggle panel */}
      {showLayerPanel && (
        <div
          className="sm:hidden mb-3 p-3 rounded-xl border"
          style={{ backgroundColor: 'var(--mantle)', borderColor: 'var(--surface0)' }}
        >
          <LayerToggle layers={kg_galaxyLayers} onChange={setKgGalaxyLayers} />
          <p className="mt-2 text-xs" style={{ color: 'var(--subtext0)' }}>
            觸控拖曳旋轉 | 雙指縮放 | 點擊查看
          </p>
        </div>
      )}

      {/* Stats bar */}
      <div className="flex items-center gap-3 mb-3 shrink-0 flex-wrap">
        <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
          {nodes.length} 個節點
        </span>
        <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
          {links.length} 個連結
        </span>
        {selectedBlock && (
          <span
            className="text-xs truncate max-w-[180px] sm:max-w-none"
            style={{ color: 'var(--blue)' }}
          >
            已選：{selectedBlock.content.slice(0, 30)}...
          </span>
        )}
      </div>

      {/* Canvas + Detail panel */}
      <div className="flex flex-1 min-h-0 gap-0">
        <div
          className="flex-1 min-h-0 relative rounded-xl overflow-hidden border"
          style={{ borderColor: 'var(--surface0)' }}
        >
          {isLoading && nodes.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <div
                className="h-8 w-8 animate-spin rounded-full border-2 border-t-transparent"
                style={{
                  borderColor: 'var(--blue)',
                  borderTopColor: 'transparent',
                }}
              />
            </div>
          ) : nodes.length === 0 ? (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
                尚無記憶區塊可視覺化
              </p>
            </div>
          ) : (
            <Suspense fallback={<div style={{ width: '100%', height: '100%', background: '#0F111E' }} />}>
              <GalaxyCanvas
                nodes={nodes}
                links={links}
                onNodeClick={handleNodeClick}
                onEmptyClick={handleEmptyClick}
                selectedNodeId={selectedBlock?.id ?? null}
              />
            </Suspense>
          )}
        </div>

        {/* Desktop Detail panel — side by side */}
        {selectedBlock && (
          <div className="hidden lg:block">
            <BlockDetailPanel block={selectedBlock} onClose={() => selectBlock(null)} />
          </div>
        )}
      </div>

      {/* Mobile Detail panel — bottom sheet (rendered outside flex container) */}
      {selectedBlock && (
        <div className="lg:hidden">
          <BlockDetailPanel block={selectedBlock} onClose={() => selectBlock(null)} />
        </div>
      )}
    </div>
  )
}
