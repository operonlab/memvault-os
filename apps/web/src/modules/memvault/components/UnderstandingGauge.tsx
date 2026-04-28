import type { KASProfile } from '@/types'
import InfoTip from './InfoTip'

interface UnderstandingGaugeProps {
  profile: KASProfile | null
  loading?: boolean
}

function GaugeBar({
  label,
  score,
  color,
  maxScore = 100,
}: {
  label: string
  score: number
  color: string
  maxScore?: number
}) {
  const pct = Math.min((score / maxScore) * 100, 100)

  return (
    <div className="flex-1 min-w-[140px]">
      <div className="flex items-end justify-between mb-2">
        <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>
          {label}
        </span>
        <span className="text-2xl font-bold tabular-nums" style={{ color }}>
          {score}
        </span>
      </div>
      <div
        className="h-3 w-full rounded-full overflow-hidden"
        style={{ backgroundColor: 'var(--surface0)' }}
      >
        <div
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}, color-mix(in srgb, ${color} 70%, white))`,
          }}
        />
      </div>
      <div className="flex justify-between mt-1">
        <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>0</span>
        <span className="text-[10px]" style={{ color: 'var(--subtext1)' }}>{maxScore}</span>
      </div>
    </div>
  )
}

function SkeletonGauge() {
  return (
    <div className="flex-1 min-w-[140px]">
      <div className="flex items-end justify-between mb-2">
        <div className="h-5 w-16 rounded animate-pulse" style={{ backgroundColor: 'var(--surface0)' }} />
        <div className="h-8 w-10 rounded animate-pulse" style={{ backgroundColor: 'var(--surface0)' }} />
      </div>
      <div className="h-3 w-full rounded-full animate-pulse" style={{ backgroundColor: 'var(--surface0)' }} />
    </div>
  )
}

export default function UnderstandingGauge({ profile, loading }: UnderstandingGaugeProps) {
  return (
    <div
      className="rounded-2xl border p-5"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: 'var(--surface0)',
      }}
    >
      <div className="flex items-center gap-2 mb-5">
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          Understanding Depth
        </h3>
        <InfoTip text="Knowledge 代表系統對你的知識圖譜理解深度（三元組 + 社群），Attitude 代表對你的偏好與工作原則理解程度。滿分各 100，以對數尺度計算。" />
      </div>

      {loading ? (
        <div className="flex flex-col sm:flex-row gap-6">
          <SkeletonGauge />
          <SkeletonGauge />
        </div>
      ) : profile === null ? (
        <p className="text-sm py-4" style={{ color: 'var(--subtext0)' }}>
          尚未建立 Profile — 執行 Session 掃描後將自動產生
        </p>
      ) : (
        <div className="flex flex-col sm:flex-row gap-6">
          <GaugeBar
            label="Knowledge"
            score={profile.knowledge_score}
            color="var(--blue)"
          />
          <GaugeBar
            label="Attitude"
            score={profile.attitude_score}
            color="var(--green)"
          />
        </div>
      )}
    </div>
  )
}
