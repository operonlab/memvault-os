import type { MemoryBlock } from '@/types'
import { relativeTime } from '../../../shared/utils/time'
import { BLOCK_TYPE_CONFIG } from '../types'

interface MemoryCardProps {
  block: MemoryBlock
  onClick?: () => void
  onDelete?: (id: string) => void
  compact?: boolean
}

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

export default function MemoryCard({ block, onClick, onDelete, compact = false }: MemoryCardProps) {
  const config = BLOCK_TYPE_CONFIG[block.block_type] ?? BLOCK_TYPE_CONFIG.general
  const confidencePct = `${Math.round(block.confidence * 100)}%`
  const badgeBg = hexToRgba(config.color, 0.18)

  if (compact) {
    return (
      <div
        onClick={onClick}
        className="flex items-start sm:items-center gap-2 sm:gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
          minHeight: 44,
        }}
      >
        <span
          className="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium mt-0.5 sm:mt-0"
          style={{
            backgroundColor: badgeBg,
            color: config.color,
            border: `1px solid ${config.color}`,
          }}
        >
          {config.label}
        </span>

        <span
          className="flex-1 text-sm line-clamp-2 sm:truncate sm:line-clamp-none"
          style={{ color: 'var(--text)' }}
        >
          {block.content}
        </span>

        {/* Tags: hidden on very small screens to prevent overflow */}
        <div className="hidden sm:flex shrink-0 items-center gap-1.5">
          {block.tags.slice(0, 2).map((tag) => (
            <span
              key={tag}
              className="rounded px-1.5 py-0.5 text-xs"
              style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
            >
              {tag}
            </span>
          ))}
        </div>

        <div className="flex flex-col sm:flex-row items-end sm:items-center gap-1 sm:gap-1.5 shrink-0">
          <span className="shrink-0 text-xs font-medium" style={{ color: config.color }}>
            {confidencePct}
          </span>
          <span className="shrink-0 text-xs" style={{ color: 'var(--subtext1)' }}>
            {relativeTime(block.updated_at)}
          </span>
        </div>

        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete(block.id)
            }}
            className="text-[12px] px-2 py-1 rounded shrink-0"
            style={{
              backgroundColor: 'rgba(243,139,168,0.1)',
              color: '#f38ba8',
              border: '1px solid rgba(243,139,168,0.2)',
            }}
          >
            刪除
          </button>
        )}
      </div>
    )
  }

  return (
    <div
      onClick={onClick}
      className="rounded-xl border p-4 cursor-pointer transition-all duration-200 active:scale-[0.98]"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: 'var(--surface0)',
      }}
      onMouseEnter={(e) => {
        const el = e.currentTarget
        el.style.transform = 'scale(1.02)'
        el.style.borderColor = config.color
      }}
      onMouseLeave={(e) => {
        const el = e.currentTarget
        el.style.transform = 'scale(1)'
        el.style.borderColor = 'var(--surface0)'
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <span
          className="rounded-full px-2.5 py-0.5 text-xs font-medium"
          style={{
            backgroundColor: badgeBg,
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

      <p className="text-sm leading-relaxed mb-3 line-clamp-3" style={{ color: 'var(--text)' }}>
        {block.content}
      </p>

      {block.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {block.tags.map((tag) => (
            <span
              key={tag}
              className="rounded px-2 py-0.5 text-xs"
              style={{ backgroundColor: 'var(--surface0)', color: 'var(--subtext0)' }}
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between">
        <p className="text-xs" style={{ color: 'var(--subtext1)' }}>
          {relativeTime(block.updated_at)}
        </p>

        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onDelete(block.id)
            }}
            className="text-[12px] px-2 py-1 rounded"
            style={{
              backgroundColor: 'rgba(243,139,168,0.1)',
              color: '#f38ba8',
              border: '1px solid rgba(243,139,168,0.2)',
            }}
          >
            刪除
          </button>
        )}
      </div>
    </div>
  )
}
