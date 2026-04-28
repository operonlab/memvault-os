import type { MemoryCardRecord } from '../types'

interface MemoryLensCardProps {
  card: MemoryCardRecord
}

export default function MemoryLensCard({ card }: MemoryLensCardProps) {
  return (
    <article
      className="rounded-2xl border p-4 h-full"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: 'var(--surface0)',
      }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em]" style={{ color: 'var(--subtext0)' }}>
            {card.layer} / {card.source_type}
          </p>
          <h3 className="text-sm font-semibold mt-1" style={{ color: 'var(--text)' }}>
            {card.title}
            {card.source === 'speculative_prefetch' && (
              <span style={{ fontSize: '0.7em', background: '#fef3c7', color: '#92400e', padding: '1px 5px', borderRadius: '3px', marginLeft: '4px' }}>
                ⚡ 預載
              </span>
            )}
          </h3>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs font-medium" style={{ color: 'var(--blue)' }}>
            {Math.round(card.confidence * 100)}%
          </div>
          {card.freshness && (
            <div className="text-[11px]" style={{ color: 'var(--subtext0)' }}>
              {card.freshness}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-3">
        <section>
          <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
            Summary
          </p>
          <p className="text-sm leading-relaxed mt-1" style={{ color: 'var(--text)' }}>
            {card.summary}
          </p>
        </section>

        <section>
          <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
            Why Relevant
          </p>
          <p className="text-sm leading-relaxed mt-1" style={{ color: 'var(--subtext0)' }}>
            {card.why_relevant}
          </p>
        </section>

        <section
          className="rounded-xl px-3 py-2"
          style={{ backgroundColor: 'color-mix(in srgb, var(--green) 10%, transparent)' }}
        >
          <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--green)' }}>
            Use Now
          </p>
          <p className="text-sm leading-relaxed mt-1" style={{ color: 'var(--text)' }}>
            {card.use_now}
          </p>
        </section>

        {card.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {card.tags.slice(0, 4).map((tag) => (
              <span
                key={tag}
                className="rounded-full px-2 py-1 text-[11px]"
                style={{
                  backgroundColor: 'var(--surface0)',
                  color: 'var(--subtext0)',
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        {card.evidence_refs.length > 0 && (
          <div className="pt-1">
            <p className="text-[11px] uppercase tracking-[0.16em]" style={{ color: 'var(--subtext1)' }}>
              Evidence
            </p>
            <ul className="mt-1 space-y-1">
              {card.evidence_refs.slice(0, 2).map((ref) => (
                <li key={`${ref.kind}-${ref.ref_id}`} className="text-xs" style={{ color: 'var(--subtext0)' }}>
                  [{ref.kind}] {ref.title}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </article>
  )
}
