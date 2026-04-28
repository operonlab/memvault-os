import type { LucideIcon } from 'lucide-react'

interface StatTileProps {
  label: string
  value: number | string
  color: string
  icon: LucideIcon
  onClick?: () => void
}

export default function StatTile({ label, value, color, icon: Icon, onClick }: StatTileProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-xl border p-4 text-left transition-colors w-full"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: 'var(--surface0)',
        cursor: onClick ? 'pointer' : 'default',
      }}
      onMouseEnter={(e) => {
        if (onClick) e.currentTarget.style.borderColor = color
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = 'var(--surface0)'
      }}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} style={{ color }} />
        <span
          className="text-[11px] uppercase tracking-[0.12em]"
          style={{ color: 'var(--subtext1)' }}
        >
          {label}
        </span>
      </div>
      <p className="text-2xl font-semibold tabular-nums" style={{ color }}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
    </button>
  )
}
