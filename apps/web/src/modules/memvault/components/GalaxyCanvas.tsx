import ForceGraph3D from '3d-force-graph'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { BlockType, GalaxyLayer, GalaxyLink, GalaxyNode } from '../types'

interface GalaxyCanvasProps {
  nodes: GalaxyNode[]
  links: GalaxyLink[]
  onNodeClick?: (node: GalaxyNode) => void
  onEmptyClick?: () => void
  selectedNodeId?: string | null
}

const CSS_VAR_MAP: Record<BlockType, string> = {
  knowledge: '--blue',
  skill: '--green',
  attitude: '--mauve',
  general: '--text',
}

function resolveColors() {
  const s = getComputedStyle(document.documentElement)
  const g = (v: string) => s.getPropertyValue(v).trim()
  const types = {} as Record<BlockType, string>
  for (const [bt, v] of Object.entries(CSS_VAR_MAP)) {
    types[bt as BlockType] = g(v)
  }
  return {
    types,
    selected: g('--peach'),
    teal: g('--teal'),
    blue: g('--blue'),
    peach: g('--peach'),
  }
}

function nodeSize(n: any): number {
  const layer = n.layer as GalaxyLayer
  switch (layer) {
    case 'summaries':
      return 4 + (n.confidence || 0.5) * 6
    case 'communities':
      return 3
    case 'triples':
      return 1 + (n.confidence || 0.5) * 0.5
    default:
      return 2 + (n.confidence || 0.5) * 12
  }
}

function nodeColor(n: any, colors: ReturnType<typeof resolveColors>): string {
  const layer = n.layer as GalaxyLayer
  switch (layer) {
    case 'summaries':
      return colors.peach
    case 'communities':
      return colors.blue
    case 'triples':
      return colors.teal
    default:
      return colors.types[n.type as BlockType] || colors.types.general
  }
}

function _nodeOpacity(n: any): number {
  const layer = n.layer as GalaxyLayer
  switch (layer) {
    case 'communities':
      return 0.35
    default:
      return 0.9
  }
}

export default function GalaxyCanvas({
  nodes,
  links,
  onNodeClick,
  onEmptyClick,
  selectedNodeId,
}: GalaxyCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const colors = useMemo(() => resolveColors(), [])
  const colorsRef = useRef(colors)
  const selectedIdRef = useRef(selectedNodeId)
  const onNodeClickRef = useRef(onNodeClick)
  const onEmptyClickRef = useRef(onEmptyClick)

  // Keep refs in sync
  useEffect(() => {
    selectedIdRef.current = selectedNodeId
  }, [selectedNodeId])
  useEffect(() => {
    onNodeClickRef.current = onNodeClick
  }, [onNodeClick])
  useEffect(() => {
    onEmptyClickRef.current = onEmptyClick
  }, [onEmptyClick])

  // Initialize graph (once)
  useEffect(() => {
    if (!containerRef.current) return
    const colors = colorsRef.current

    const graph = ForceGraph3D()(containerRef.current)
      .backgroundColor('#0F111E')
      // ── Nodes ──
      .nodeVal((n: any) => nodeSize(n))
      .nodeColor((n: any) => nodeColor(n, colors))
      .nodeOpacity(0.9)
      .nodeResolution(16)
      .nodeThreeObjectExtend(true)
      .nodeThreeObject((n: any) => {
        if (n.id !== selectedIdRef.current) return undefined as any
        const nVal = nodeSize(n)
        const r = Math.cbrt(nVal) * graph.nodeRelSize()
        const torus = new THREE.Mesh(
          new THREE.TorusGeometry(r * 1.6, r * 0.1, 12, 48),
          new THREE.MeshBasicMaterial({
            color: new THREE.Color(colors.selected),
            transparent: true,
            opacity: 0.65,
          }),
        )
        torus.rotation.x = Math.PI * 0.42 // Saturn-like tilt
        return torus
      })
      .nodeLabel((n: any) => {
        const conf = Math.round((n.confidence || 0) * 100)
        const layerLabel = n.layer || 'block'
        return `<div style="text-align:center;font-family:system-ui;padding:4px 8px">
          <div style="font-size:13px;font-weight:600">${n.label || ''}</div>
          <div style="font-size:11px;opacity:0.7;margin-top:2px">${layerLabel} · ${conf}%</div>
        </div>`
      })
      // ── Links + particles ──
      .linkDirectionalParticles(2)
      .linkDirectionalParticleWidth(1.5)
      .linkDirectionalParticleSpeed(0.005)
      .linkDirectionalParticleColor(() => 'rgba(180, 190, 254, 0.7)')
      .linkColor(() => 'rgba(180, 190, 254, 0.25)')
      .linkWidth((l: any) => 0.5 + (l.strength || 0.5) * 1.5)
      .linkOpacity(0.6)
      // ── Physics ──
      .d3AlphaDecay(0.02)
      .d3VelocityDecay(0.3)

    // Tighten the cluster: weaker repulsion + shorter link distance
    graph.d3Force('charge')?.strength(-30).distanceMax(80)
    graph.d3Force('link')?.distance(20).strength(0.8)

    graph
      // ── Interaction ──
      .onNodeClick((node: any) => {
        onNodeClickRef.current?.(node as GalaxyNode)
      })
      .onNodeDragEnd((node: any) => {
        // Pin node at drop position
        node.fx = node.x
        node.fy = node.y
        node.fz = node.z
      })
      .onNodeRightClick((node: any) => {
        // Unpin node
        node.fx = undefined
        node.fy = undefined
        node.fz = undefined
      })
      .onBackgroundClick(() => {
        onEmptyClickRef.current?.()
      })
      .warmupTicks(80)
      .cooldownTicks(200)

    graphRef.current = graph

    // Auto-fit camera after simulation settles
    setTimeout(() => {
      graph.zoomToFit(600, 50)
    }, 1000)

    // Resize observer
    const ro = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) graph.width(width).height(height)
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      const g = graphRef.current
      if (g) {
        // Dispose all GPU resources (geometry + material) created by nodeThreeObject
        g.scene().traverse((obj: any) => {
          if (obj.geometry) obj.geometry.dispose()
          if (obj.material) {
            if (Array.isArray(obj.material)) obj.material.forEach((m: any) => m.dispose())
            else obj.material.dispose()
          }
        })
        g.pauseAnimation()
        g.graphData({ nodes: [], links: [] })
        g._destructor()
      }
      graphRef.current = null
      if (containerRef.current) containerRef.current.innerHTML = ''
    }
  }, [])

  // Update graph data when nodes/links change
  useEffect(() => {
    if (!graphRef.current) return
    graphRef.current.graphData({
      nodes: nodes.map((n) => ({
        id: n.id,
        label: n.label,
        type: n.type,
        confidence: n.confidence,
        layer: n.layer,
      })),
      links: links.map((l) => ({
        source: l.source,
        target: l.target,
        strength: l.strength,
      })),
    })
    setTimeout(() => {
      graphRef.current?.zoomToFit(400, 50)
    }, 1200)
  }, [nodes, links])

  // Update Saturn ring on selection change
  useEffect(() => {
    if (!graphRef.current) return
    graphRef.current.nodeThreeObject(graphRef.current.nodeThreeObject())
  }, [selectedNodeId])

  return <div ref={containerRef} style={{ width: '100%', height: '100%', position: 'relative' }} />
}
