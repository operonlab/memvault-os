import type { BlockType } from '../types'
import { BLOCK_TYPE_CONFIG } from '../types'

interface BlockTypeFilterProps {
  activeType: string | null
  onChange: (type: string | null) => void
  counts?: Record<string, number>
}

const BLOCK_TYPES: BlockType[] = ['knowledge', 'skill', 'attitude', 'general']

export default function BlockTypeFilter({ activeType, onChange, counts }: BlockTypeFilterProps) {
  return (
    <div className="flex flex-wrap gap-2">
      <button
        onClick={() => onChange(null)}
        className="rounded-lg px-3 py-2 text-sm font-medium cursor-pointer transition-colors"
        style={{
          ...(activeType === null
            ? {
                backgroundColor: 'color-mix(in srgb, var(--text) 18%, transparent)',
                color: 'var(--text)',
                border: '1px solid var(--text)',
              }
            : {
                backgroundColor: 'var(--surface0)',
                color: 'var(--subtext0)',
                border: '1px solid transparent',
              }),
          minHeight: 44,
        }}
      >
        全部{counts !== undefined ? ` (${Object.values(counts).reduce((a, b) => a + b, 0)})` : ''}
      </button>

      {BLOCK_TYPES.map((type) => {
        const config = BLOCK_TYPE_CONFIG[type]
        const isActive = activeType === type
        const count = counts?.[type]

        return (
          <button
            key={type}
            onClick={() => onChange(type)}
            className="rounded-lg px-3 py-2 text-sm font-medium cursor-pointer transition-colors"
            style={{
              ...(isActive
                ? {
                    backgroundColor: `color-mix(in srgb, ${config.color} 18%, transparent)`,
                    color: config.color,
                    border: `1px solid ${config.color}`,
                  }
                : {
                    backgroundColor: 'var(--surface0)',
                    color: 'var(--subtext0)',
                    border: '1px solid transparent',
                  }),
              minHeight: 44,
            }}
          >
            {config.label}
            {count !== undefined ? ` (${count})` : ''}
          </button>
        )
      })}
    </div>
  )
}
