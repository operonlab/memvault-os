import { useState } from 'react'
import { relativeTime } from '../../../shared/utils/time'
import type { SessionSummary } from '../types'
import { BLOCK_TYPE_CONFIG, type BlockType } from '../types'

interface SessionCardProps {
  session: SessionSummary
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('zh-TW', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatSessionName(source: string): string {
  // Session names like "fix-auth-middleware" → "Fix Auth Middleware"
  if (!source) return '未命名'
  return source
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function SessionCard({ session }: SessionCardProps) {
  const [expanded, setExpanded] = useState(false)

  const isSameDay = new Date(session.first_at).toDateString() === new Date(session.last_at).toDateString()

  return (
    <button
      onClick={() => setExpanded(!expanded)}
      className="w-full text-left rounded-xl border p-4 transition-all duration-200"
      style={{
        backgroundColor: expanded ? 'var(--mantle)' : 'var(--base)',
        borderColor: expanded ? 'var(--blue)' : 'var(--surface0)',
      }}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h4
            className="text-sm font-medium truncate"
            style={{ color: 'var(--text)' }}
            title={session.source_session}
          >
            {formatSessionName(session.source_session)}
          </h4>
          <p className="text-xs mt-1" style={{ color: 'var(--subtext0)' }}>
            {relativeTime(session.last_at)}
          </p>
        </div>

        {/* Block count badge */}
        <span
          className="shrink-0 rounded-full px-2.5 py-1 text-xs font-medium"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--blue) 15%, transparent)',
            color: 'var(--blue)',
          }}
        >
          {session.block_count} blocks
        </span>
      </div>

      {/* Block type badges */}
      <div className="flex flex-wrap gap-1.5 mt-3">
        {session.block_types.map((bt) => {
          const cfg = BLOCK_TYPE_CONFIG[bt as BlockType]
          const color = cfg?.color ?? 'var(--subtext0)'
          const label = cfg?.label ?? bt
          return (
            <span
              key={bt}
              className="rounded px-2 py-0.5 text-[11px] font-medium"
              style={{
                backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)`,
                color,
              }}
            >
              {label}
            </span>
          )
        })}
      </div>

      {/* Expanded details */}
      {expanded && (
        <div
          className="mt-3 pt-3 border-t space-y-2 text-xs"
          style={{ borderColor: 'var(--surface0)', color: 'var(--subtext0)' }}
        >
          <div className="flex justify-between">
            <span>Session ID</span>
            <span className="truncate max-w-[200px] font-mono" style={{ color: 'var(--text)' }}>
              {session.source_session}
            </span>
          </div>
          <div className="flex justify-between">
            <span>首次記錄</span>
            <span style={{ color: 'var(--text)' }}>{formatDate(session.first_at)}</span>
          </div>
          <div className="flex justify-between">
            <span>最後記錄</span>
            <span style={{ color: 'var(--text)' }}>
              {isSameDay ? formatDate(session.last_at).split(' ').pop() : formatDate(session.last_at)}
            </span>
          </div>
        </div>
      )}
    </button>
  )
}
