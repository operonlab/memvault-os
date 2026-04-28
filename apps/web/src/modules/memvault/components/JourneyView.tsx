import { useState } from 'react'
import AttitudeTimeline from './AttitudeTimeline'
import InfoTip from './InfoTip'
import SessionCard from './SessionCard'
import { useSessions } from '../hooks/queries'

export default function JourneyView() {
  const [page, setPage] = useState(1)
  const { data, isLoading } = useSessions(page)

  const sessions = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / 20)

  return (
    <div className="mx-auto max-w-3xl px-3 py-4 sm:px-4 sm:py-5 lg:px-6 lg:py-6">
      {/* Header */}
      <div className="flex items-center gap-2 mb-6">
        <div
          className="h-3 w-3 rounded-full shrink-0"
          style={{ backgroundColor: 'var(--lavender)' }}
        />
        <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
          Journey
        </h1>
        <InfoTip text="依時間軸瀏覽所有 session 的記憶萃取歷程。每張卡片顯示該 session 產出的 block 數量與類型分佈。展開可查看詳細時間戳與 session ID。" />
        {total > 0 && (
          <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
            {total} sessions
          </span>
        )}
      </div>

      {/* Timeline */}
      <div className="relative">
        {/* Vertical timeline line */}
        {sessions.length > 0 && (
          <div
            className="absolute left-5 top-0 bottom-0 w-px"
            style={{ backgroundColor: 'var(--surface1)' }}
          />
        )}

        {/* Loading */}
        {isLoading && sessions.length === 0 && (
          <div className="flex justify-center py-16">
            <div
              className="h-8 w-8 animate-spin rounded-full border-2 border-t-transparent"
              style={{ borderColor: 'var(--lavender)', borderTopColor: 'transparent' }}
            />
          </div>
        )}

        {/* Empty */}
        {!isLoading && sessions.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 gap-2">
            <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
              尚無 Session 記錄
            </p>
            <p className="text-xs" style={{ color: 'var(--subtext1)' }}>
              Session 掃描後會自動出現在這裡
            </p>
          </div>
        )}

        {/* Session cards in timeline */}
        <div className="space-y-3">
          {sessions.map((session, idx) => {
            // Show date separator when day changes
            const prevSession = sessions[idx - 1]
            const currDate = new Date(session.last_at).toLocaleDateString('zh-TW', {
              year: 'numeric',
              month: 'long',
              day: 'numeric',
            })
            const prevDate = prevSession
              ? new Date(prevSession.last_at).toLocaleDateString('zh-TW', {
                  year: 'numeric',
                  month: 'long',
                  day: 'numeric',
                })
              : null
            const showDateSep = currDate !== prevDate

            return (
              <div key={session.source_session}>
                {showDateSep && (
                  <div className="flex items-center gap-3 mb-3 mt-1 pl-3">
                    <div
                      className="h-2.5 w-2.5 rounded-full shrink-0 z-10"
                      style={{ backgroundColor: 'var(--lavender)' }}
                    />
                    <span
                      className="text-xs font-medium"
                      style={{ color: 'var(--subtext0)' }}
                    >
                      {currDate}
                    </span>
                  </div>
                )}
                <div className="pl-12 relative">
                  {/* Timeline dot */}
                  <div
                    className="absolute left-[17px] top-5 h-1.5 w-1.5 rounded-full z-10"
                    style={{ backgroundColor: 'var(--surface2)' }}
                  />
                  <SessionCard session={session} />
                </div>
              </div>
            )
          })}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 mt-6">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
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
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
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
        )}
      </div>

      {/* Attitude Timeline at bottom */}
      <div className="mt-8 pt-6 border-t" style={{ borderColor: 'var(--surface0)' }}>
        <AttitudeTimeline />
      </div>
    </div>
  )
}
