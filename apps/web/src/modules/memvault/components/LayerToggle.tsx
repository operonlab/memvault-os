import type { GalaxyLayer } from '../types'
import { KG_LAYER_CONFIG } from '../types'

function hexToRgba(cssVar: string, alpha: number): string {
  return `color-mix(in srgb, ${cssVar} ${Math.round(alpha * 100)}%, transparent)`
}

interface LayerToggleProps {
  layers: Set<GalaxyLayer>
  onChange: (layers: Set<GalaxyLayer>) => void
}

const LAYER_ORDER: GalaxyLayer[] = ['blocks', 'triples', 'communities', 'summaries']

export default function LayerToggle({ layers, onChange }: LayerToggleProps) {
  const toggle = (layer: GalaxyLayer) => {
    const next = new Set(layers)
    if (next.has(layer)) {
      next.delete(layer)
    } else {
      next.add(layer)
    }
    onChange(next)
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs mr-0.5" style={{ color: 'var(--subtext0)' }}>
        圖層
      </span>
      {LAYER_ORDER.map((layer) => {
        const config = KG_LAYER_CONFIG[layer]
        const active = layers.has(layer)

        return (
          <button
            key={layer}
            onClick={() => toggle(layer)}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-all duration-200"
            style={{
              backgroundColor: active ? hexToRgba(config.color, 0.18) : 'var(--mantle)',
              color: active ? config.color : 'var(--subtext0)',
              border: `1px solid ${active ? config.color : 'var(--surface0)'}`,
              opacity: active ? 1 : 0.6,
              minHeight: 36,
            }}
          >
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{
                backgroundColor: active ? config.color : 'var(--subtext0)',
              }}
            />
            {config.label}
          </button>
        )
      })}
    </div>
  )
}
