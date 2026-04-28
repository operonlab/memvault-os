interface SearchBarProps {
  value: string
  onChange: (value: string) => void
  onSearch: () => void
  loading?: boolean
  resultCount?: number
  onClear?: () => void
}

export default function SearchBar({
  value,
  onChange,
  onSearch,
  loading = false,
  resultCount,
  onClear,
}: SearchBarProps) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !loading && value.trim()) {
      onSearch()
    }
  }

  const isDisabled = loading || !value.trim()

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="搜尋記憶..."
          className="flex-1 rounded-lg border px-4 py-2.5 text-sm outline-none transition-colors"
          style={{
            backgroundColor: 'var(--base)',
            borderColor: 'var(--surface0)',
            color: 'var(--text)',
            minHeight: 44,
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = 'var(--blue)'
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = 'var(--surface0)'
          }}
        />

        <button
          onClick={onSearch}
          disabled={isDisabled}
          className="rounded-lg px-4 py-2.5 text-sm font-medium transition-opacity shrink-0"
          style={{
            backgroundColor: isDisabled ? 'var(--surface0)' : 'var(--blue)',
            color: isDisabled ? 'var(--subtext1)' : 'var(--crust)',
            cursor: isDisabled ? 'not-allowed' : 'pointer',
            opacity: isDisabled ? 0.6 : 1,
            minHeight: 44,
            minWidth: 64,
          }}
        >
          {loading ? (
            <span
              className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-t-transparent"
              style={{ borderColor: 'var(--crust)', borderTopColor: 'transparent' }}
            />
          ) : (
            '搜尋'
          )}
        </button>
      </div>

      {resultCount !== undefined && value.trim() && (
        <div className="flex items-center gap-2 px-1">
          <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
            找到 {resultCount} 筆結果
          </span>
          {onClear && (
            <button
              onClick={onClear}
              className="text-xs underline-offset-2 hover:underline py-1"
              style={{ color: 'var(--subtext0)', minHeight: 44 }}
            >
              清除
            </button>
          )}
        </div>
      )}
    </div>
  )
}
