import { useMemo, useState } from 'react'
import { relativeTime } from '../../../shared/utils/time'
import { useDeleteAttitude, useUpdateAttitude } from '../hooks/mutations'
import { useAttitudeHistory, useAttitudes } from '../hooks/queries'
import type { AttitudeFact } from '../types'
import InfoTip from './InfoTip'

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

function HistoryChain({ history }: { history: AttitudeFact[] }) {
  if (history.length === 0) return null

  return (
    <div className="mt-2 pl-3 border-l-2 space-y-2" style={{ borderColor: 'var(--mauve)' }}>
      {history.map((h) => (
        <div key={h.id} className="relative">
          <div
            className="absolute -left-[calc(0.75rem+1px)] top-1.5 h-2 w-2 rounded-full"
            style={{
              backgroundColor: h.operation === 'ADD' ? 'var(--green)' : 'var(--yellow)',
            }}
          />
          <div
            className="rounded-lg border px-3 py-2 text-xs"
            style={{
              backgroundColor: 'var(--base)',
              borderColor: 'var(--surface0)',
              opacity: 0.5 + h.confidence * 0.5,
            }}
          >
            <div className="flex items-center justify-between mb-1 gap-2">
              <span
                className="rounded px-1.5 py-0.5 font-medium shrink-0"
                style={{
                  backgroundColor:
                    h.operation === 'ADD'
                      ? hexToRgba('var(--green)', 0.15)
                      : hexToRgba('var(--yellow)', 0.15),
                  color: h.operation === 'ADD' ? 'var(--green)' : 'var(--yellow)',
                }}
              >
                {h.operation}
              </span>
              <span className="shrink-0" style={{ color: 'var(--subtext0)' }}>
                {relativeTime(h.created_at)}
              </span>
            </div>
            <p style={{ color: 'var(--text)' }}>{h.fact}</p>
            <div className="mt-1.5 flex items-center gap-2">
              <div
                className="h-1 flex-1 rounded-full overflow-hidden"
                style={{ backgroundColor: 'var(--surface0)' }}
              >
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${Math.round(h.confidence * 100)}%`,
                    backgroundColor: 'var(--mauve)',
                  }}
                />
              </div>
              <span style={{ color: 'var(--subtext0)' }}>{Math.round(h.confidence * 100)}%</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function AttitudeCard({
  attitude,
  onShowHistory,
  historyData,
  showHistory,
  onDelete,
  onUpdate,
}: {
  attitude: AttitudeFact
  onShowHistory: () => void
  historyData: AttitudeFact[]
  showHistory: boolean
  onDelete: (id: string) => void
  onUpdate: (id: string, fact: string, category: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(attitude.fact)

  return (
    <div
      className="rounded-xl border p-3 transition-all duration-200"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: showHistory ? 'var(--mauve)' : 'var(--surface0)',
      }}
    >
      {/* Content row */}
      <div className="flex items-start gap-2 mb-2">
        {editing ? (
          <div className="flex-1 flex flex-col sm:flex-row gap-1.5">
            <input
              type="text"
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              className="flex-1 rounded border px-2 py-1.5 text-sm outline-none"
              style={{
                backgroundColor: 'var(--base)',
                borderColor: 'var(--mauve)',
                color: 'var(--text)',
                minHeight: 44,
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  onUpdate(attitude.id, editText, attitude.category)
                  setEditing(false)
                } else if (e.key === 'Escape') {
                  setEditText(attitude.fact)
                  setEditing(false)
                }
              }}
            />
            <div className="flex gap-1.5">
              <button
                onClick={() => {
                  onUpdate(attitude.id, editText, attitude.category)
                  setEditing(false)
                }}
                className="flex-1 sm:flex-none rounded px-3 py-2 text-xs font-medium"
                style={{ backgroundColor: 'var(--green)', color: 'var(--base)', minHeight: 44 }}
              >
                儲存
              </button>
              <button
                onClick={() => {
                  setEditText(attitude.fact)
                  setEditing(false)
                }}
                className="flex-1 sm:flex-none rounded px-3 py-2 text-xs"
                style={{
                  backgroundColor: 'var(--surface0)',
                  color: 'var(--subtext0)',
                  minHeight: 44,
                }}
              >
                取消
              </button>
            </div>
          </div>
        ) : (
          <p className="text-sm flex-1" style={{ color: 'var(--text)' }}>
            {attitude.fact}
          </p>
        )}
        {!editing && (
          <span
            className="rounded px-1.5 py-0.5 text-xs shrink-0 mt-0.5"
            style={{
              backgroundColor:
                attitude.operation === 'ADD'
                  ? hexToRgba('var(--green)', 0.15)
                  : hexToRgba('var(--yellow)', 0.15),
              color: attitude.operation === 'ADD' ? 'var(--green)' : 'var(--yellow)',
            }}
          >
            {attitude.operation}
          </span>
        )}
      </div>

      {/* Bottom bar: confidence + actions */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div
            className="h-1 w-16 rounded-full overflow-hidden"
            style={{ backgroundColor: 'var(--surface0)' }}
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.round(attitude.confidence * 100)}%`,
                backgroundColor: 'var(--mauve)',
                opacity: attitude.confidence,
              }}
            />
          </div>
          <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
            {Math.round(attitude.confidence * 100)}%
          </span>
        </div>
        <div className="flex items-center gap-1 flex-wrap justify-end">
          {!editing && (
            <button
              onClick={() => setEditing(true)}
              className="text-xs py-1 px-2 rounded transition-colors"
              style={{ color: 'var(--subtext0)', minHeight: 36 }}
              title="編輯"
            >
              編輯
            </button>
          )}
          <button
            onClick={() => {
              if (confirm('確定刪除此態度記錄？')) onDelete(attitude.id)
            }}
            className="text-xs py-1 px-2 rounded transition-colors"
            style={{ color: 'var(--red)', minHeight: 36 }}
            title="刪除"
          >
            刪除
          </button>
          <button
            onClick={onShowHistory}
            className="text-xs py-1 px-2 rounded transition-colors"
            style={{ color: 'var(--mauve)', minHeight: 36 }}
          >
            {showHistory ? '隱藏歷史' : '版本鏈'}
          </button>
        </div>
      </div>

      {showHistory && <HistoryChain history={historyData} />}
    </div>
  )
}

const CATEGORY_LABELS: Record<string, string> = {
  workflow: '工作流程',
  tool_behavior: '工具行為',
  config: '設定',
  architecture: '架構',
  preference: '偏好',
  naming: '命名',
  technical: '技術',
  principle: '原則',
}

export default function AttitudeTimeline() {
  const { data: attitudes = [], isLoading } = useAttitudes()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const { data: attitudeHistory = [] } = useAttitudeHistory(expandedId)
  const deleteAttitudeMutation = useDeleteAttitude()
  const updateAttitudeMutation = useUpdateAttitude()

  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set())

  const grouped = useMemo(() => {
    const map: Record<string, AttitudeFact[]> = {}
    for (const a of attitudes) {
      if (!map[a.category]) map[a.category] = []
      map[a.category].push(a)
    }
    return Object.entries(map).sort((a, b) => b[1].length - a[1].length)
  }, [attitudes])

  const handleShowHistory = (id: string) => {
    setExpandedId(expandedId === id ? null : id)
  }

  const toggleCategory = (category: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <span
          className="inline-block h-3 w-3 rounded-full"
          style={{ backgroundColor: 'var(--mauve)' }}
        />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          態度演進
        </h3>
        <InfoTip text="態度是從對話中自動提煉的偏好、習慣與工作原則。每條態度有信心度（隨時間衰減）和版本鏈（記錄 ADD/UPDATE 演進歷程）。按類別分組顯示：工作流程、工具行為、設定、架構等。" />
        <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
          {attitudes.length} 條
        </span>
      </div>

      {isLoading && attitudes.length === 0 ? (
        <div className="flex justify-center py-8">
          <div
            className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
            style={{ borderColor: 'var(--mauve)', borderTopColor: 'transparent' }}
          />
        </div>
      ) : attitudes.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 gap-2">
          <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
            尚無態度記錄
          </p>
          <p className="text-xs" style={{ color: 'var(--subtext1)' }}>
            態度將在對話中自動提煉
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {grouped.map(([category, categoryAttitudes]) => {
            const isCollapsed = collapsedCategories.has(category)
            const label = CATEGORY_LABELS[category] ?? category
            return (
              <div
                key={category}
                className="rounded-xl border overflow-hidden"
                style={{
                  backgroundColor: 'var(--mantle)',
                  borderColor: isCollapsed ? 'var(--surface0)' : 'var(--mauve)',
                }}
              >
                {/* Category header — clickable toggle */}
                <button
                  onClick={() => toggleCategory(category)}
                  className="flex items-center gap-2 w-full px-4 text-left transition-colors"
                  style={{
                    backgroundColor: isCollapsed
                      ? 'var(--mantle)'
                      : hexToRgba('var(--mauve)', 0.06),
                    minHeight: 48,
                    paddingTop: '0.625rem',
                    paddingBottom: '0.625rem',
                  }}
                >
                  <span
                    className="text-xs transition-transform duration-200 shrink-0"
                    style={{
                      color: 'var(--mauve)',
                      display: 'inline-block',
                      transform: isCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
                    }}
                  >
                    ▼
                  </span>
                  <span
                    className="text-sm font-medium flex-1 text-left"
                    style={{ color: 'var(--text)' }}
                  >
                    {label}
                  </span>
                  <span
                    className="rounded-full px-2 py-0.5 text-xs font-medium shrink-0"
                    style={{
                      backgroundColor: hexToRgba('var(--mauve)', 0.15),
                      color: 'var(--mauve)',
                    }}
                  >
                    {categoryAttitudes.length}
                  </span>
                </button>

                {/* Collapsible content */}
                {!isCollapsed && (
                  <div className="px-3 pb-3 pt-1 space-y-2 sm:px-4">
                    {categoryAttitudes.map((a) => (
                      <AttitudeCard
                        key={a.id}
                        attitude={a}
                        onShowHistory={() => handleShowHistory(a.id)}
                        historyData={expandedId === a.id ? attitudeHistory : []}
                        showHistory={expandedId === a.id}
                        onDelete={(id) => deleteAttitudeMutation.mutate(id)}
                        onUpdate={(id, fact, cat) =>
                          updateAttitudeMutation.mutate({ id, data: { fact, category: cat } })
                        }
                      />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
