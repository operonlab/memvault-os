interface PaginationProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
}

export default function Pagination({ page, totalPages, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center gap-3 pt-4">
      <button
        type="button"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        className="rounded-lg px-3 py-2 text-sm transition-colors"
        style={{
          backgroundColor: 'var(--surface0)',
          color: page <= 1 ? 'var(--surface2)' : 'var(--text)',
          opacity: page <= 1 ? 0.5 : 1,
          cursor: page <= 1 ? 'default' : 'pointer',
          minHeight: 44,
        }}
      >
        上一頁
      </button>

      <span className="text-xs tabular-nums" style={{ color: 'var(--subtext0)' }}>
        {page} / {totalPages}
      </span>

      <button
        type="button"
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        className="rounded-lg px-3 py-2 text-sm transition-colors"
        style={{
          backgroundColor: 'var(--surface0)',
          color: page >= totalPages ? 'var(--surface2)' : 'var(--text)',
          opacity: page >= totalPages ? 0.5 : 1,
          cursor: page >= totalPages ? 'default' : 'pointer',
          minHeight: 44,
        }}
      >
        下一頁
      </button>
    </div>
  )
}
