import { useMemo } from 'react'
import type { MemoryBlock } from '@/types'
import type {
  Community,
  CommunitySummary,
  GalaxyLayer,
  GalaxyLink,
  GalaxyNode,
  Triple,
} from '../types'

interface UseGalaxyOptions {
  blocks: MemoryBlock[]
  triples: Triple[]
  communities: Community[]
  summaries: CommunitySummary[]
  visibleLayers: Set<GalaxyLayer>
}

export function useGalaxy({
  blocks,
  triples,
  communities,
  summaries,
  visibleLayers,
}: UseGalaxyOptions) {
  const nodes: GalaxyNode[] = useMemo(() => {
    const result: GalaxyNode[] = []

    if (visibleLayers.has('blocks')) {
      for (const b of blocks) {
        result.push({
          id: b.id,
          label: b.content.slice(0, 40),
          type: b.block_type,
          confidence: b.confidence,
          layer: 'blocks',
        })
      }
    }

    if (visibleLayers.has('triples')) {
      // Limit to first 200 for performance
      for (const t of triples.slice(0, 200)) {
        result.push({
          id: t.id,
          label: `${t.subject} → ${t.predicate}`,
          type: 'knowledge',
          confidence: 0.5,
          layer: 'triples',
        })
      }
    }

    if (visibleLayers.has('communities')) {
      for (const c of communities) {
        result.push({
          id: c.id,
          label: c.name,
          type: 'knowledge',
          confidence: 0.7,
          layer: 'communities',
        })
      }
    }

    if (visibleLayers.has('summaries')) {
      for (const s of summaries) {
        result.push({
          id: s.id,
          label: s.summary.slice(0, 50),
          type: 'knowledge',
          confidence: 0.85,
          layer: 'summaries',
        })
      }
    }

    return result
  }, [blocks, triples, communities, summaries, visibleLayers])

  const links: GalaxyLink[] = useMemo(() => {
    const result: GalaxyLink[] = []
    const nodeIds = new Set(nodes.map((n) => n.id))

    // Block-to-block links (tag overlap) — only if blocks visible
    if (visibleLayers.has('blocks')) {
      for (let i = 0; i < blocks.length; i++) {
        for (let j = i + 1; j < blocks.length; j++) {
          const shared = blocks[i].tags.filter((t) => blocks[j].tags.includes(t))
          if (shared.length > 0) {
            result.push({
              source: blocks[i].id,
              target: blocks[j].id,
              strength: shared.length / Math.max(blocks[i].tags.length, blocks[j].tags.length, 1),
            })
          }
        }
      }
    }

    // Summary → Community links
    if (visibleLayers.has('summaries') && visibleLayers.has('communities')) {
      for (const s of summaries) {
        if (nodeIds.has(s.community_id)) {
          result.push({
            source: s.id,
            target: s.community_id,
            strength: 0.8,
          })
        }
      }
    }

    return result
  }, [nodes, blocks, summaries, visibleLayers])

  return { nodes, links }
}
