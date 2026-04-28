import { Search, X } from 'lucide-react'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { kgApi } from '../../api/kg'
import { useTriplesFiltered } from '../../hooks/queries'
import { useDeleteTriple } from '../../hooks/mutations'
import type { Triple } from '../../types'
import EmptyState from '../shared/EmptyState'
import Pagination from '../shared/Pagination'
import { useQuery } from '@tanstack/react-query'

export default function TriplesPage() {
  const [searchParams] = useSearchParams()
  const initialSubject = searchParams.get('subject') ?? ''

  const [page, setPage] = useState(1)
  const [predicate, setPredicate] = useState('')
  const [subject, setSubject] = useState(initialSubject)
  const [searchMode, setSearchMode] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const { data, isLoading, isError } = useTriplesFiltered(
    page,
    predicate || undefined,
    subject || undefined,
  )

  const {
    data: searchResults,
    isLoading: searchLoading,
  } = useQuery({
    queryKey: ['memvault', 'kg', 'triples-search', searchQuery],
    queryFn: () => kgApi.searchTriples(searchQuery, 20),
    enabled: searchMode && !!searchQuery.trim(),
    staleTime: 5 * 60 * 1000,
  })

  const deleteTriple = useDeleteTriple()

  const totalPages = data ? Math.ceil(data.total / 20) : 0
  const triples: Triple[] = searchMode ? (searchResults ?? []) : (data?.items ?? [])
  const totalCount = searchMode ? (searchResults?.length ?? 0) : (data?.total ?? 0)

  return (
    <div className="mx-auto max-w-5xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6">
      {/* Header */}
      <div className="mb-4">
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
          Knowledge Triples
          <span className="text-xs font-normal ml-2" style={{ color: 'var(--blue)' }}>L0</span>
        </h1>
        <p className="text-xs mt-0.5" style={{ color: 'var(--subtext0)' }}>
          結構化三元組：主詞 → 謂詞 → 受詞 — {totalCount.toLocaleString()} 筆
        </p>
      </div>

      {/* Search / Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <button
          type="button"
          onClick={() => {
            setSearchMode(!searchMode)
            setSearchQuery('')
          }}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors"
          style={{
            backgroundColor: searchMode
              ? 'color-mix(in srgb, var(--blue) 18%, var(--surface0))'
              : 'var(--surface0)',
            color: searchMode ? 'var(--blue)' : 'var(--subtext1)',
          }}
        >
          <Search size={12} />
          語意搜尋
        </button>

        {searchMode ? (
          <div className="flex-1 flex items-center gap-2">
            <input
              type="text"
              placeholder="輸入搜尋語意…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 rounded-lg border px-3 py-1.5 text-xs"
              style={{
                backgroundColor: 'var(--base)',
                borderColor: 'var(--blue)',
                color: 'var(--text)',
                minHeight: 32,
              }}
            />
          </div>
        ) : (
          <>
            <input
              type="text"
              placeholder="Predicate 篩選…"
              value={predicate}
              onChange={(e) => { setPredicate(e.target.value); setPage(1) }}
              className="rounded-lg border px-3 py-1.5 text-xs"
              style={{
                backgroundColor: 'var(--base)',
                borderColor: 'var(--surface0)',
                color: 'var(--text)',
                minHeight: 32,
              }}
            />
            <div className="relative">
              <input
                type="text"
                placeholder="Subject 篩選…"
                value={subject}
                onChange={(e) => { setSubject(e.target.value); setPage(1) }}
                className="rounded-lg border px-3 py-1.5 text-xs pr-7"
                style={{
                  backgroundColor: 'var(--base)',
                  borderColor: 'var(--surface0)',
                  color: 'var(--text)',
                  minHeight: 32,
                }}
              />
              {subject && (
                <button
                  type="button"
                  onClick={() => { setSubject(''); setPage(1) }}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--subtext1)' }}
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {/* Content */}
      {(searchMode ? searchLoading : isLoading) ? (
        <EmptyState loading color="var(--blue)" />
      ) : isError ? (
        <EmptyState error="無法載入三元組資料" />
      ) : triples.length === 0 ? (
        <EmptyState empty emptyTitle="無符合條件的三元組" emptySubtitle="嘗試調整篩選條件" />
      ) : (
        <div className="space-y-2">
          {triples.map((t) => (
            <TripleRow key={t.id} triple={t} onDelete={(id) => deleteTriple.mutate(id)} />
          ))}
        </div>
      )}

      {!searchMode && <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />}
    </div>
  )
}

function TripleRow({ triple: t, onDelete }: { triple: Triple; onDelete: (id: string) => void }) {
  return (
    <div
      className="group rounded-lg border p-3 transition-colors"
      style={{ backgroundColor: 'var(--base)', borderColor: 'var(--surface0)' }}
    >
      {t.display_zh && (
        <p className="text-xs mb-1.5 leading-relaxed" style={{ color: 'var(--text)' }}>
          {t.display_zh}
        </p>
      )}
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-xs font-medium break-all" style={{ color: 'var(--teal)' }}>
          {t.subject}
        </span>
        <span
          className="px-1.5 py-0.5 rounded text-[10px] shrink-0"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--blue) 15%, transparent)',
            color: 'var(--blue)',
          }}
        >
          {t.predicate}
        </span>
        <span className="text-xs break-all flex-1" style={{ color: 'var(--subtext0)' }}>
          {t.object}
        </span>
      </div>
      <div className="flex items-center justify-between mt-2">
        <div className="flex items-center gap-2">
          {t.topic && (
            <span
              className="rounded px-1.5 py-0.5 text-[10px]"
              style={{
                backgroundColor: 'color-mix(in srgb, var(--lavender) 12%, transparent)',
                color: 'var(--lavender)',
              }}
            >
              {t.topic}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => onDelete(t.id)}
          className="text-[11px] px-2 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity"
          style={{
            backgroundColor: 'rgba(243,139,168,0.1)',
            color: '#f38ba8',
          }}
        >
          刪除
        </button>
      </div>
    </div>
  )
}
