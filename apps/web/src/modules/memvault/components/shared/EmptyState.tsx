interface EmptyStateProps {
  loading?: boolean
  error?: string | null
  empty?: boolean
  color?: string
  emptyTitle?: string
  emptySubtitle?: string
}

export default function EmptyState({
  loading,
  error,
  empty,
  color = 'var(--blue)',
  emptyTitle = '無資料',
  emptySubtitle,
}: EmptyStateProps) {
  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div
          className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
          style={{ borderColor: color, borderTopColor: 'transparent' }}
        />
      </div>
    )
  }

  if (error) {
    return (
      <div className="py-8 text-center">
        <p className="text-xs" style={{ color: 'var(--red)' }}>
          {error}
        </p>
      </div>
    )
  }

  if (empty) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
          {emptyTitle}
        </p>
        {emptySubtitle && (
          <p className="text-xs mt-1" style={{ color: 'var(--subtext1)' }}>
            {emptySubtitle}
          </p>
        )}
      </div>
    )
  }

  return null
}
