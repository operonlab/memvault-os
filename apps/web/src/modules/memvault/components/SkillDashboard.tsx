import { useState } from 'react'
import { useSkills } from '../hooks/queries'
import type { SkillProfile } from '../types'
import InfoTip from './InfoTip'

const SKILL_DESCRIPTIONS: Record<string, string> = {
  'smart-search': '智慧搜尋：跨多個平台（Reddit、知乎、Stack Overflow 等）進行深度搜尋與綜合分析。',
  maestro: '任務指揮：將複雜任務拆解為子任務，協調多個 agent 並行執行。',
  dev: '開發輔助：結合 maestro + forge 全流程輔助程式碼撰寫、除錯、重構。',
  'team-tasks': '團隊任務：任務分派、進度追蹤、多人協作管理。',
  'code-review-interceptor': '程式碼審查：自動攔截並審查程式碼變更，檢查品質與安全性。',
  'tdd-enforcer': '測試驅動開發：強制執行 RED-GREEN-REFACTOR 流程。',
  blueprint: '藍圖規劃：專案架構設計、實作計畫制定與文件產出。',
  'create-skill': '技能建立：建立新的 Claude Code skill，包含 SKILL.md 撰寫。',
  'frontend-design': '前端設計：打造高品質的 UI 介面、元件開發與視覺調整。',
  'openclaw-mentor': 'OpenClaw 導師：OpenClaw 平台相關的指導與教學。',
  playground: '實驗場：建立互動式 HTML playground 進行快速原型驗證。',
  verification: '驗證：自動化測試執行與結果驗證。',
  'verification-before-completion': '完成前驗證：在提交或建立 PR 前執行最終驗證檢查。',
  'notebookllm-visual': 'NotebookLLM 視覺化：將內容轉換為資訊圖表或簡報格式。',
  'notebooklm-visual': 'NotebookLLM 視覺化：將內容轉換為資訊圖表或簡報格式。',
  'notebook-bridge': 'NotebookLLM 橋接：上傳來源至 NotebookLLM、產生 Audio Overview。',
  'diagram-gen': '圖表生成：自動產生架構圖、流程圖、序列圖等。',
  'deep-research': '深度研究：針對特定主題進行全面深入的研究分析。',
  'workshop-sync': 'Workshop 同步：同步 Workshop 平台各模組狀態。',
  'context-engineer': 'Context 工程：最佳化 LLM 上下文管理與 prompt 設計。',
  'blink-builder': 'Blink Shell 建置：從 GPL 原始碼建置並側載 Blink Shell 到 iPhone。',
  brainstorming: '腦力激盪：探索多種方案、設計功能、思考各種可能性。',
  'claude-code-headless': 'Claude Code 無頭模式：透過 claude -p 執行批次腳本任務。',
  'codex-cli-headless': 'Codex CLI 無頭模式：透過 codex exec 執行批次任務。',
  'codex-headless': 'Codex 無頭模式：透過 Codex CLI 執行批次任務。',
  forge: '鍛造：從 idea 到 shipped 的全流程實作 pipeline。',
  'gemini-cli-headless': 'Gemini CLI 無頭模式：透過 gemini -p 執行批次任務。',
  'git-worktrees': 'Git Worktree：同時在多個分支工作、隔離開發環境。',
  'image-gen': '圖片生成：透過 Grok 或其他 AI 服務生成圖片。',
  'image-prompt': '圖片 Prompt 生成：撰寫高品質的 AI 生圖提示詞。',
  'macos-ui-automation': 'macOS UI 自動化：透過 AppleScript 控制系統對話框與視窗操作。',
  'mcp-builder': 'MCP Server 建置：建立自訂 MCP server 與工具。',
  'model-mentor': '模型推薦：根據任務需求推薦最適合的 AI 模型。',
  'quote-consultant': '報價顧問：估算專案報價、定價策略建議。',
  'sandbox-patterns': 'Sandbox 模式：sandbox_execute 的最佳實踐與使用模式。',
  'skill-lifecycle': '技能維護：skill 生命週期管理、定期維護與清理。',
  'skill-optimizer': '技能優化：根據使用回饋改善 skill 的效能與品質。',
  'spec-kit': '規格驅動開發：撰寫規格文件，以 spec 驅動實作流程。',
  'sync-config': '設定同步：將 MCP、skill 等設定同步至 Gemini/Codex CLI。',
}

const LEVEL_BADGE: Record<string, { label: string; color: string }> = {
  novice: { label: '新手', color: 'var(--yellow)' },
  proficient: { label: '熟練', color: 'var(--blue)' },
  expert: { label: '專家', color: 'var(--green)' },
}

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

function rateColor(rate: number): string {
  if (rate >= 0.8) return 'var(--green)'
  if (rate >= 0.5) return 'var(--yellow)'
  return 'var(--red)'
}

function SkillBar({
  skill,
  expanded,
  onToggle,
}: {
  skill: SkillProfile
  expanded: boolean
  onToggle: () => void
}) {
  const barColor = rateColor(skill.success_rate)
  const width = Math.max(skill.success_rate * 100, 2)
  const badge = LEVEL_BADGE[skill.proficiency_level] ?? LEVEL_BADGE.novice

  return (
    <div
      className="rounded-xl border p-3 transition-all duration-200 cursor-pointer"
      style={{
        backgroundColor: 'var(--mantle)',
        borderColor: expanded ? 'var(--green)' : 'var(--surface0)',
        minHeight: 44,
      }}
      onClick={onToggle}
    >
      <div className="flex items-start sm:items-center gap-2 sm:gap-3 mb-2">
        <span
          className="text-sm font-medium flex-1 min-w-0 flex items-center gap-1.5 break-all sm:break-normal sm:truncate"
          style={{ color: 'var(--text)' }}
        >
          <span className="truncate">{skill.skill_name}</span>
          {SKILL_DESCRIPTIONS[skill.skill_name] && (
            <span className="shrink-0">
              <InfoTip text={SKILL_DESCRIPTIONS[skill.skill_name]} />
            </span>
          )}
        </span>
        <div
          className="flex items-center gap-2 shrink-0 text-xs flex-wrap justify-end"
          style={{ color: 'var(--subtext0)' }}
        >
          <span>{skill.total_uses} 次</span>
          <span style={{ color: barColor }}>{Math.round(skill.success_rate * 100)}%</span>
          <span
            className="rounded px-1.5 py-0.5 text-xs font-medium"
            style={{
              backgroundColor: hexToRgba(badge.color, 0.15),
              color: badge.color,
            }}
          >
            {badge.label}
          </span>
        </div>
      </div>

      {/* Success rate bar */}
      <div
        className="h-2 w-full rounded-full overflow-hidden"
        style={{ backgroundColor: 'var(--surface0)' }}
      >
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${width}%`,
            backgroundColor: barColor,
          }}
        />
      </div>

      {/* Expanded profile details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-2.5" style={{ borderColor: 'var(--surface0)' }}>
          {/* Stats row */}
          <div className="flex flex-wrap gap-3 text-xs" style={{ color: 'var(--subtext0)' }}>
            <span>
              近期使用：<strong style={{ color: 'var(--text)' }}>{skill.recent_uses}</strong>
            </span>
            {skill.avg_duration_ms != null && (
              <span>
                平均耗時：
                <strong style={{ color: 'var(--text)' }}>
                  {Math.round(skill.avg_duration_ms)}ms
                </strong>
              </span>
            )}
            {skill.auto_rate != null && (
              <span>
                自動率：
                <strong style={{ color: 'var(--text)' }}>
                  {Math.round(skill.auto_rate * 100)}%
                </strong>
              </span>
            )}
            {skill.health_score != null && (
              <span>
                健康度：
                <strong style={{ color: rateColor(skill.health_score) }}>
                  {Math.round(skill.health_score * 100)}%
                </strong>
              </span>
            )}
          </div>

          {/* Common patterns */}
          {skill.common_patterns && skill.common_patterns.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                常見模式
              </p>
              <div className="flex flex-wrap gap-1.5">
                {skill.common_patterns.map((pattern) => (
                  <span
                    key={pattern}
                    className="rounded px-2 py-0.5 text-xs"
                    style={{
                      backgroundColor: hexToRgba('var(--blue)', 0.12),
                      color: 'var(--blue)',
                    }}
                  >
                    {pattern}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Learned preferences */}
          {skill.learned_preferences && Object.keys(skill.learned_preferences).length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                學習偏好
              </p>
              <div className="space-y-1">
                {Object.entries(skill.learned_preferences).map(([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border p-2 text-xs flex items-center gap-2"
                    style={{
                      backgroundColor: 'var(--base)',
                      borderColor: 'var(--surface0)',
                      color: 'var(--subtext0)',
                    }}
                  >
                    <span className="font-medium" style={{ color: 'var(--text)' }}>
                      {key}
                    </span>
                    <span>{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Evolution notes */}
          {skill.evolution_notes && skill.evolution_notes.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--subtext0)' }}>
                演化紀錄
              </p>
              <ul
                className="space-y-0.5 text-xs list-disc pl-4"
                style={{ color: 'var(--subtext0)' }}
              >
                {skill.evolution_notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Pitfalls */}
          {skill.pitfalls && skill.pitfalls.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--peach)' }}>
                注意事項
              </p>
              <ul
                className="space-y-0.5 text-xs list-disc pl-4"
                style={{ color: 'var(--subtext0)' }}
              >
                {skill.pitfalls.map((pitfall, i) => (
                  <li key={i}>
                    {Object.entries(pitfall)
                      .map(([k, v]) => `${k}: ${String(v)}`)
                      .join(', ')}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Last synced */}
          {skill.last_synced_at && (
            <p className="text-xs" style={{ color: 'var(--subtext1)' }}>
              最後同步：{new Date(skill.last_synced_at).toLocaleString('zh-TW')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function SkillDashboard() {
  const { data: skills = [], isLoading } = useSkills()
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)

  const handleToggle = (name: string) => {
    setExpandedSkill(expandedSkill === name ? null : name)
  }

  const sorted = [...skills].sort((a, b) => b.total_uses - a.total_uses)

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <span
          className="inline-block h-3 w-3 rounded-full"
          style={{ backgroundColor: 'var(--green)' }}
        />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          技能概況
        </h3>
        <InfoTip
          text={
            '長條 = 成功率，徽章 = 熟練等級（新手/熟練/專家）\n展開可查看常見模式、學習偏好、演化紀錄'
          }
        />
        <span className="text-xs" style={{ color: 'var(--subtext0)' }}>
          {skills.length} 項
        </span>
      </div>

      {isLoading && skills.length === 0 ? (
        <div className="flex justify-center py-8">
          <div
            className="h-6 w-6 animate-spin rounded-full border-2 border-t-transparent"
            style={{ borderColor: 'var(--green)', borderTopColor: 'transparent' }}
          />
        </div>
      ) : skills.length === 0 ? (
        <div
          className="flex flex-col items-center justify-center py-12 gap-2 rounded-xl border"
          style={{ backgroundColor: 'var(--mantle)', borderColor: 'var(--surface0)' }}
        >
          <p className="text-sm" style={{ color: 'var(--subtext0)' }}>
            尚無技能資料
          </p>
          <p className="text-xs text-center px-4" style={{ color: 'var(--subtext1)' }}>
            技能概況將在 Skill 使用後由系統自動彙整
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.map((skill) => (
            <SkillBar
              key={skill.skill_name}
              skill={skill}
              expanded={expandedSkill === skill.skill_name}
              onToggle={() => handleToggle(skill.skill_name)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
