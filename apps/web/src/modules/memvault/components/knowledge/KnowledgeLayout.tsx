import { Layers, GitBranch, Network, Sparkles, LayoutDashboard } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'

const TABS = [
  { to: '/memvault/knowledge', label: 'Dashboard', icon: LayoutDashboard, color: 'var(--lavender)', end: true },
  { to: '/memvault/knowledge/blocks', label: 'Blocks', icon: Layers, color: 'var(--peach)', end: false },
  { to: '/memvault/knowledge/triples', label: 'Triples', icon: GitBranch, color: 'var(--blue)', end: false },
  { to: '/memvault/knowledge/communities', label: 'Communities', icon: Network, color: 'var(--green)', end: false },
  { to: '/memvault/knowledge/insights', label: 'Insights', icon: Sparkles, color: 'var(--mauve)', end: false },
] as const

export default function KnowledgeLayout() {
  return (
    <div className="flex flex-col h-full">
      <nav
        className="flex items-center gap-1 px-3 py-2 border-b shrink-0 overflow-x-auto"
        style={{
          backgroundColor: 'var(--mantle)',
          borderColor: 'var(--surface0)',
        }}
      >
        {TABS.map((tab) => {
          const Icon = tab.icon
          return (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap"
              style={({ isActive }) => ({
                backgroundColor: isActive
                  ? `color-mix(in srgb, ${tab.color} 15%, var(--surface0))`
                  : 'transparent',
                color: isActive ? tab.color : 'var(--subtext1)',
              })}
            >
              <span
                className="inline-block h-1.5 w-1.5 rounded-full shrink-0"
                style={{ backgroundColor: tab.color }}
              />
              <Icon size={14} />
              <span className="hidden sm:inline">{tab.label}</span>
            </NavLink>
          )
        })}
      </nav>

      <div className="flex-1 min-h-0 overflow-y-auto">
        <Outlet />
      </div>
    </div>
  )
}
