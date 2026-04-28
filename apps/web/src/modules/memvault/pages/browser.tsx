import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { MemoryBlock } from '@/types'
import { memvaultApi, type SyncScanResult, type SyncStats } from '../api'
import AttitudeTimeline from '../components/AttitudeTimeline'
import BlockTypeFilter from '../components/BlockTypeFilter'
import CascadeSearchBar from '../components/CascadeSearchBar'
import InfoTip from '../components/InfoTip'
import KgExplorerPanel from '../components/KgExplorerPanel'
import MemoryCard from '../components/MemoryCard'
import MemoryLensCard from '../components/MemoryLensCard'
import ProfileWidget from '../components/ProfileWidget'
import SearchBar from '../components/SearchBar'
import SkillDashboard from '../components/SkillDashboard'
import { useDeleteBlock } from '../hooks/mutations'
import { useBlocks, useProfile } from '../hooks/queries'
import { useMemorySearch } from '../hooks/useMemorySearch'
import { useMemvaultStore } from '../stores'
import type {
  BrowserTab,
  LoadBudget,
  MemoryCardRecord,
  MemoryQueryOptions,
  TaskMode,
  ThinkingMode,
} from '../types'

const TABS: { key: BrowserTab; label: string; hint: string }[] = [
  { key: 'fast', label: 'Fast Memory', hint: '最小負荷、最快取用' },
  { key: 'working', label: 'Working Memory', hint: '當前任務上下文' },
  { key: 'deep', label: 'Deep Memory', hint: '完整證據與圖譜' },
  { key: 'skills', label: 'Skills', hint: '能力與態度演化' },
]

const DEFAULT_QUERY_OPTIONS: MemoryQueryOptions = {
  taskMode: 'build',
  thinkingMode: 'auto',
  loadBudget: 'standard',
  consumer: 'human',
  topK: 6,
}

function CollapsibleSection({
  title,
  color,
  defaultOpen = true,
  info,
  children,
}: {
  title: string
  color: string
  defaultOpen?: boolean
  info?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div
      className="rounded-xl border"
      style={{
        borderColor: open ? color : 'var(--surface0)',
      }}
    >
      <div
        className="flex items-center gap-2 w-full px-3 py-3 sm:px-4 transition-colors"
        style={{
          backgroundColor: open ? `color-mix(in srgb, ${color} 6%, transparent)` : 'var(--mantle)',
          borderRadius: open ? '0.75rem 0.75rem 0 0' : '0.75rem',
        }}
      >
        <button
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 text-left flex-1 min-h-[44px]"
        >
          <span
            className="text-xs transition-transform duration-200"
            style={{
              color,
              display: 'inline-block',
              transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
            }}
          >
            ▼
          </span>
          <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            {title}
          </span>
        </button>
        {info && <InfoTip text={info} />}
        <div className="flex-1" />
      </div>
      {open && <div className="px-3 pb-4 sm:px-4">{children}</div>}
    </div>
  )
}

function ViewToggle({
  mode,
  onChange,
}: {
  mode: 'grid' | 'list'
  onChange: (m: 'grid' | 'list') => void
}) {
  return (
    <div
      className="flex rounded-lg overflow-hidden border"
      style={{ borderColor: 'var(--surface0)' }}
    >
      {(['grid', 'list'] as const).map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className="px-3 py-1.5 text-xs font-medium transition-colors"
          style={{
            backgroundColor: mode === m ? 'var(--surface0)' : 'var(--mantle)',
            color: mode === m ? 'var(--text)' : 'var(--subtext0)',
            minHeight: 44,
          }}
        >
          {m === 'grid' ? '卡片' : '列表'}
        </button>
      ))}
    </div>
  )
}

function Pagination({
  page,
  total,
  pageSize,
  onPageChange,
}: {
  page: number
  total: number
  pageSize: number
  onPageChange: (p: number) => void
}) {
  const totalPages = Math.ceil(total / pageSize)
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center gap-2 mt-6">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        className="rounded-lg px-3 py-2 text-sm transition-colors"
        style={{
          backgroundColor: 'var(--surface0)',
          color: page <= 1 ? 'var(--subtext0)' : 'var(--text)',
          cursor: page <= 1 ? 'not-allowed' : 'pointer',
          opacity: page <= 1 ? 0.5 : 1,
          minHeight: 44,
          minWidth: 44,
        }}
      >
        上一頁
      </button>
      <span className="text-sm" style={{ color: 'var(--subtext0)' }}>
        {page} / {totalPages}
      </span>
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        className="rounded-lg px-3 py-2 text-sm transition-colors"
        style={{
          backgroundColor: 'var(--surface0)',
          color: page >= totalPages ? 'var(--subtext0)' : 'var(--text)',
          cursor: page >= totalPages ? 'not-allowed' : 'pointer',
          opacity: page >= totalPages ? 0.5 : 1,
          minHeight: 44,
          minWidth: 44,
        }}
      >
        下一頁
      </button>
    </div>
  )
}

function SyncWidget({ onSynced }: { onSynced?: () => void }) {
  const [stats, setStats] = useState<SyncStats | null>(null)
  const [scanning, setScanning] = useState(false)
  const [lastResult, setLastResult] = useState<SyncScanResult | null>(null)

  const fetchStats = useCallback(async () => {
    try {
      setStats(await memvaultApi.syncStats())
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    fetchStats()
  }, [fetchStats])

  const runScan = async () => {
    setScanning(true)
    setLastResult(null)
    try {
      const result = await memvaultApi.syncScan()
      setLastResult(result)
      await fetchStats()
      if (result.synced > 0) onSynced?.()
    } catch {
      /* ignore */
    } finally {
      setScanning(false)
    }
  }

  return (
    <div
      className="rounded-xl border p-4"
      style={{ backgroundColor: 'var(--mantle)', borderColor: 'var(--surface0)' }}
    >
      <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--text)' }}>
        Session 掃描
      </h3>

      {stats && (
        <div className="grid grid-cols-2 gap-2 mb-3 text-xs" style={{ color: 'var(--subtext0)' }}>
          <div className="flex justify-between">
            <span>已收錄</span>
            <span style={{ color: 'var(--green)' }}>{stats.synced}</span>
          </div>
          <div className="flex justify-between">
            <span>Session 數</span>
            <span style={{ color: 'var(--text)' }}>{stats.total}</span>
          </div>
          <div className="flex justify-between">
            <span>失敗</span>
            <span style={{ color: stats.failed > 0 ? 'var(--red)' : 'var(--subtext0)' }}>
              {stats.failed}
            </span>
          </div>
          <div className="flex justify-between">
            <span>略過</span>
            <span>{stats.skipped}</span>
          </div>
        </div>
      )}

      <button
        onClick={runScan}
        disabled={scanning}
        className="w-full rounded-lg px-3 py-2.5 text-sm font-medium transition-colors"
        style={{
          backgroundColor: scanning ? 'var(--surface0)' : 'var(--blue)',
          color: scanning ? 'var(--subtext0)' : 'var(--base)',
          cursor: scanning ? 'wait' : 'pointer',
          minHeight: 44,
        }}
      >
        {scanning ? '掃描中...' : '掃描 Session'}
      </button>

      {lastResult && (
        <p className="mt-2 text-xs" style={{ color: 'var(--subtext0)' }}>
          {lastResult.synced > 0
            ? `新收錄 ${lastResult.synced} 筆記憶`
            : `全部已收錄 (${lastResult.already} 筆)`}
          {lastResult.failed > 0 && (
            <span style={{ color: 'var(--red)' }}> / {lastResult.failed} 失敗</span>
          )}
        </p>
      )}
    </div>
  )
}

function QueryControls({
  options,
  onChange,
}: {
  options: MemoryQueryOptions
  onChange: (next: Partial<MemoryQueryOptions>) => void
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
      <SelectField<TaskMode>
        label="Task"
        value={options.taskMode}
        options={[
          { value: 'lookup', label: 'Lookup' },
          { value: 'decide', label: 'Decide' },
          { value: 'build', label: 'Build' },
          { value: 'reflect', label: 'Reflect' },
        ]}
        onChange={(value) => onChange({ taskMode: value })}
      />
      <SelectField<ThinkingMode>
        label="Thinking"
        value={options.thinkingMode}
        options={[
          { value: 'auto', label: 'Auto' },
          { value: 'fast', label: 'Fast' },
          { value: 'slow', label: 'Slow' },
        ]}
        onChange={(value) => onChange({ thinkingMode: value })}
      />
      <SelectField<LoadBudget>
        label="Load Budget"
        value={options.loadBudget}
        options={[
          { value: 'light', label: 'Light' },
          { value: 'standard', label: 'Standard' },
          { value: 'deep', label: 'Deep' },
        ]}
        onChange={(value) => onChange({ loadBudget: value })}
      />
    </div>
  )
}

function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="rounded-xl border px-3 py-2.5 text-sm"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
          color: 'var(--text)',
          minHeight: 44,
        }}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function StrategyStrip({
  activeTab,
  hint,
  highlights,
  thinkingMode,
}: {
  activeTab: BrowserTab
  hint: string
  highlights: string[]
  thinkingMode?: string
}) {
  return (
    <div
      className="rounded-2xl border p-4"
      style={{
        background: 'linear-gradient(135deg, color-mix(in srgb, var(--blue) 10%, var(--mantle)), var(--mantle))',
        borderColor: 'color-mix(in srgb, var(--blue) 22%, var(--surface0))',
      }}
    >
      <p className="text-[11px] uppercase tracking-[0.18em]" style={{ color: 'var(--subtext1)' }}>
        {activeTab} memory
      </p>
      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3 mt-2">
        <div>
          <h2 className="text-xl font-semibold" style={{ color: 'var(--text)' }}>
            {hint}
          </h2>
          <p className="text-sm mt-1" style={{ color: 'var(--subtext0)' }}>
            {thinkingMode ? `實際路徑：${thinkingMode}` : '輸入查詢後，系統會組裝對應層級記憶。'}
          </p>
        </div>
        {highlights.length > 0 && (
          <div className="lg:max-w-md">
            <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
              Highlights
            </p>
            <ul className="mt-1 space-y-1">
              {highlights.slice(0, 2).map((item) => (
                <li key={item} className="text-sm" style={{ color: 'var(--text)' }}>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function MemoryLensGrid({
  cards,
  emptyMessage,
}: {
  cards: MemoryCardRecord[]
  emptyMessage: string
}) {
  if (cards.length === 0) {
    return (
      <div
        className="rounded-2xl border px-5 py-10 text-center"
        style={{ borderColor: 'var(--surface0)', backgroundColor: 'var(--mantle)' }}
      >
        <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
          {emptyMessage}
        </p>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {cards.map((card) => (
        <MemoryLensCard key={card.id} card={card} />
      ))}
    </div>
  )
}

function BlockDetailDrawer({ block, onClose }: { block: MemoryBlock; onClose: () => void }) {
  return (
    <>
      <div
        className="fixed inset-0 z-40"
        style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
        onClick={onClose}
      />
      <div
        className="fixed bottom-0 left-0 right-0 z-50 rounded-t-2xl border-t p-5 max-h-[70vh] overflow-y-auto"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
        }}
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            記憶詳情
          </h3>
          <button
            onClick={onClose}
            className="flex items-center justify-center rounded-lg text-sm"
            style={{ color: 'var(--subtext0)', minWidth: 44, minHeight: 44 }}
          >
            關閉
          </button>
        </div>

        <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text)' }}>
          {block.content}
        </p>

        <div className="flex flex-col gap-2 text-xs" style={{ color: 'var(--subtext0)' }}>
          <div className="flex justify-between">
            <span>類型</span>
            <span style={{ color: 'var(--text)' }}>{block.block_type}</span>
          </div>
          <div className="flex justify-between">
            <span>信心度</span>
            <span style={{ color: 'var(--text)' }}>{Math.round(block.confidence * 100)}%</span>
          </div>
          {block.source_session && (
            <div className="flex justify-between">
              <span>來源工作階段</span>
              <span className="truncate max-w-[160px]" style={{ color: 'var(--text)' }}>
                {block.source_session}
              </span>
            </div>
          )}
          {block.tags.length > 0 && (
            <div>
              <span className="block mb-1">標籤</span>
              <div className="flex flex-wrap gap-1">
                {block.tags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded px-2 py-0.5"
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
        </div>
      </div>
    </>
  )
}

export default function MemoryBrowser() {
  const {
    page,
    pageSize,
    selectedBlock,
    viewMode,
    filters,
    selectBlock,
    setPage,
    setFilters,
    setViewMode,
    kg_activeTab,
    setKgActiveTab,
  } = useMemvaultStore()

  const [showSidebar, setShowSidebar] = useState(false)
  const [queryOptions, setQueryOptions] = useState<MemoryQueryOptions>(DEFAULT_QUERY_OPTIONS)

  const blocksQuery = useBlocks(page, pageSize, filters)
  const profileQuery = useProfile()
  const queryClient = useQueryClient()
  const deleteBlockMutation = useDeleteBlock()

  const searchOptions: MemoryQueryOptions = {
    ...queryOptions,
    consumer: kg_activeTab === 'deep' ? 'ui' : 'human',
  }
  const { query, results, isSearching, setQuery, searchNow, clear } = useMemorySearch(searchOptions)

  const blocks = blocksQuery.data?.items ?? []
  const total = blocksQuery.data?.total ?? 0
  const strategy = results?.strategy
  const tabHint = TABS.find((tab) => tab.key === kg_activeTab)?.hint ?? ''

  const activeCards =
    kg_activeTab === 'fast'
      ? results?.fast_cards ?? []
      : kg_activeTab === 'working'
        ? results?.working_cards ?? []
        : results?.deep_cards ?? []

  const handleDeleteBlock = (id: string) => {
    if (!window.confirm('確定要刪除這筆記憶嗎？')) return
    deleteBlockMutation.mutate(id, {
      onSuccess: () => {
        if (selectedBlock?.id === id) selectBlock(null)
      },
    })
  }

  return (
    <div className="mx-auto max-w-7xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6 lg:py-6">
      <div className="flex items-center justify-between mb-4 gap-2">
        {kg_activeTab === 'deep' && <ViewToggle mode={viewMode} onChange={setViewMode} />}
        <button
          onClick={() => setShowSidebar(true)}
          className="lg:hidden flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors ml-auto"
          style={{
            backgroundColor: 'var(--surface0)',
            color: 'var(--subtext0)',
            minHeight: 44,
          }}
        >
          KAS 狀態
        </button>
      </div>

      <div
        className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-4 sm:mb-6"
      >
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setKgActiveTab(tab.key)}
            className="rounded-xl border px-3 py-3 text-left transition-colors"
            style={{
              borderColor:
                kg_activeTab === tab.key
                  ? 'color-mix(in srgb, var(--blue) 40%, var(--surface0))'
                  : 'var(--surface0)',
              backgroundColor:
                kg_activeTab === tab.key
                  ? 'color-mix(in srgb, var(--blue) 10%, var(--mantle))'
                  : 'var(--mantle)',
              minHeight: 78,
            }}
          >
            <div className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              {tab.label}
            </div>
            <div className="text-xs mt-1" style={{ color: 'var(--subtext0)' }}>
              {tab.hint}
            </div>
          </button>
        ))}
      </div>

      <div className="flex gap-4 lg:gap-6">
        <div className="flex-1 min-w-0 space-y-4">
          <StrategyStrip
            activeTab={kg_activeTab}
            hint={tabHint}
            highlights={results?.highlights ?? []}
            thinkingMode={strategy?.thinking_mode_used}
          />

          {kg_activeTab !== 'skills' && (
            <>
              <SearchBar
                value={query}
                onChange={setQuery}
                onSearch={searchNow}
                loading={isSearching}
                resultCount={query.trim() ? activeCards.length : undefined}
                onClear={clear}
              />
              <QueryControls options={queryOptions} onChange={(next) => setQueryOptions((prev) => ({ ...prev, ...next }))} />
            </>
          )}

          {kg_activeTab === 'fast' && (
            <MemoryLensGrid
              cards={activeCards}
              emptyMessage={query.trim() ? 'Fast path 目前沒有可立即注入的記憶。' : '輸入查詢後，Fast Memory 會回傳最小負荷的可用知識卡。'}
            />
          )}

          {kg_activeTab === 'working' && (
            <MemoryLensGrid
              cards={activeCards}
              emptyMessage={query.trim() ? 'Working Memory 目前沒有形成足夠上下文。' : '輸入查詢後，Working Memory 會組裝當前任務上下文。'}
            />
          )}

          {kg_activeTab === 'deep' && (
            <div className="space-y-4">
              <MemoryLensGrid
                cards={activeCards}
                emptyMessage={query.trim() ? 'Slow path 尚未找到可展開的深層證據。' : '輸入查詢後，Deep Memory 會展開摘要、關聯與深層證據。'}
              />

              <CollapsibleSection
                title="Deep Evidence Explorer"
                color="var(--blue)"
                info="保留舊的 KG 視角做慢想探索；新的 deep cards 會先把重點壓縮給您，再決定是否展開圖譜。"
              >
                <div className="mb-4">
                  <CascadeSearchBar />
                </div>
                <KgExplorerPanel />
              </CollapsibleSection>

              <CollapsibleSection
                title="Deep Archive"
                color="var(--peach)"
                info="原始 blocks 保留為深層證據層，供人工巡覽與除錯。"
              >
                <div className="mb-4">
                  <BlockTypeFilter
                    activeType={filters.blockType}
                    onChange={(type) => setFilters({ blockType: type as typeof filters.blockType })}
                  />
                </div>

                {blocksQuery.isLoading && (
                  <div className="flex items-center justify-center py-20">
                    <div
                      className="h-8 w-8 animate-spin rounded-full border-2 border-t-transparent"
                      style={{ borderColor: 'var(--blue)', borderTopColor: 'transparent' }}
                    />
                  </div>
                )}

                {!blocksQuery.isLoading && blocks.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-16 gap-2">
                    <p className="text-base" style={{ color: 'var(--subtext0)' }}>
                      尚無記憶區塊
                    </p>
                  </div>
                )}

                {blocks.length > 0 && (
                  <div
                    className={
                      viewMode === 'grid'
                        ? 'grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 sm:gap-4'
                        : 'flex flex-col gap-2'
                    }
                  >
                    {blocks.map((block) => (
                      <MemoryCard
                        key={block.id}
                        block={block}
                        compact={viewMode === 'list'}
                        onClick={() => selectBlock(block)}
                        onDelete={handleDeleteBlock}
                      />
                    ))}
                  </div>
                )}

                <Pagination page={page} total={total} pageSize={pageSize} onPageChange={setPage} />
              </CollapsibleSection>
            </div>
          )}

          {kg_activeTab === 'skills' && (
            <div className="space-y-4">
              <CollapsibleSection
                title="技能熟練度"
                color="var(--green)"
                info="技能面板保留，用來對照 Fast / Working / Deep 路徑是否真的提升工作品質。"
              >
                <SkillDashboard />
              </CollapsibleSection>
              <CollapsibleSection
                title="態度演進"
                color="var(--mauve)"
                info="態度是 Fast Memory 的重要來源；這裡保留完整演進脈絡。"
              >
                <AttitudeTimeline />
              </CollapsibleSection>
            </div>
          )}
        </div>

        <div className="hidden lg:flex lg:w-72 lg:flex-col lg:gap-4 lg:shrink-0">
          <ProfileWidget profile={profileQuery.data ?? null} loading={profileQuery.isLoading} />
          <SyncWidget
            onSynced={() => queryClient.invalidateQueries({ queryKey: ['memvault', 'blocks'] })}
          />

          {results && (
            <div
              className="rounded-xl border p-4"
              style={{ backgroundColor: 'var(--mantle)', borderColor: 'var(--surface0)' }}
            >
              <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
                Query Strategy
              </p>
              <div className="mt-3 space-y-2 text-sm" style={{ color: 'var(--subtext0)' }}>
                <div className="flex justify-between">
                  <span>Task</span>
                  <span style={{ color: 'var(--text)' }}>{results.strategy.task_mode}</span>
                </div>
                <div className="flex justify-between">
                  <span>Thinking</span>
                  <span style={{ color: 'var(--text)' }}>{results.strategy.thinking_mode_used}</span>
                </div>
                <div className="flex justify-between">
                  <span>Budget</span>
                  <span style={{ color: 'var(--text)' }}>{results.strategy.load_budget}</span>
                </div>
              </div>
            </div>
          )}

          {selectedBlock && (
            <div
              className="rounded-xl border p-5"
              style={{
                backgroundColor: 'var(--mantle)',
                borderColor: 'var(--surface0)',
              }}
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                  記憶詳情
                </h3>
                <button
                  onClick={() => selectBlock(null)}
                  className="text-xs py-1 px-2"
                  style={{ color: 'var(--subtext0)' }}
                >
                  關閉
                </button>
              </div>

              <p className="text-sm leading-relaxed mb-3" style={{ color: 'var(--text)' }}>
                {selectedBlock.content}
              </p>

              <div className="flex flex-col gap-2 text-xs" style={{ color: 'var(--subtext0)' }}>
                <div className="flex justify-between">
                  <span>類型</span>
                  <span style={{ color: 'var(--text)' }}>{selectedBlock.block_type}</span>
                </div>
                <div className="flex justify-between">
                  <span>信心度</span>
                  <span style={{ color: 'var(--text)' }}>
                    {Math.round(selectedBlock.confidence * 100)}%
                  </span>
                </div>
                {selectedBlock.source_session && (
                  <div className="flex justify-between">
                    <span>來源工作階段</span>
                    <span className="truncate max-w-[120px]" style={{ color: 'var(--text)' }}>
                      {selectedBlock.source_session}
                    </span>
                  </div>
                )}
                {selectedBlock.tags.length > 0 && (
                  <div>
                    <span className="block mb-1">標籤</span>
                    <div className="flex flex-wrap gap-1">
                      {selectedBlock.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded px-2 py-0.5"
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
              </div>
            </div>
          )}
        </div>
      </div>

      {selectedBlock && (
        <div className="lg:hidden">
          <BlockDetailDrawer block={selectedBlock} onClose={() => selectBlock(null)} />
        </div>
      )}

      {showSidebar && (
        <>
          <div
            className="lg:hidden fixed inset-0 z-40"
            style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
            onClick={() => setShowSidebar(false)}
          />
          <div
            className="lg:hidden fixed bottom-0 left-0 right-0 z-50 rounded-t-2xl border-t p-5 max-h-[80vh] overflow-y-auto"
            style={{
              backgroundColor: 'var(--mantle)',
              borderColor: 'var(--surface0)',
            }}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                KAS 狀態
              </h3>
              <button
                onClick={() => setShowSidebar(false)}
                className="flex items-center justify-center rounded-lg text-sm"
                style={{ color: 'var(--subtext0)', minWidth: 44, minHeight: 44 }}
              >
                關閉
              </button>
            </div>
            <div className="space-y-4">
              <ProfileWidget profile={profileQuery.data ?? null} loading={profileQuery.isLoading} />
              <SyncWidget
                onSynced={() => {
                  queryClient.invalidateQueries({ queryKey: ['memvault', 'blocks'] })
                  setShowSidebar(false)
                }}
              />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
