'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import ArticlePanel from './ArticlePanel'

const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), {
  ssr: false,
})

interface GraphNode {
  id: string
  label: string
  description: string
  status: 'production' | 'development' | 'planned'
  article_path: string | null
  category: string
  x?: number
  y?: number
}

interface GraphEdge {
  from: string
  to: string
  label: string
  source?: string
  target?: string
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  categories: Record<string, { colour: string; label: string }>
}

const STATUS_INDICATORS: Record<string, string> = {
  production: '#22c55e',
  development: '#f59e0b',
  planned: '#94a3b8',
}

export default function ModuleMap() {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [hoveredNode, setHoveredNode] = useState<string | null>(null)
  const graphRef = useRef<any>(null)

  useEffect(() => {
    fetch('/api/wiki/graph')
      .then((res) => res.json())
      .then((data: GraphData) => setGraphData(data))
      .catch((err) => console.error('Failed to load graph:', err))
  }, [])

  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node as GraphNode)
  }, [])

  const handleNodeHover = useCallback((node: any) => {
    setHoveredNode(node?.id ?? null)
  }, [])

  const nodeCanvasObject = useCallback(
    (node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      if (!graphData) return
      const n = node as GraphNode
      const category = graphData.categories[n.category]
      const colour = category?.colour ?? '#6b7280'
      const isPlanned = n.status === 'planned'
      const isHovered = hoveredNode === n.id
      const isSelected = selectedNode?.id === n.id

      const radius = isHovered || isSelected ? 22 : 18
      const x = node.x ?? 0
      const y = node.y ?? 0

      // Node circle
      ctx.beginPath()
      ctx.arc(x, y, radius, 0, 2 * Math.PI)
      ctx.fillStyle = isPlanned ? '#1e293b' : colour
      ctx.globalAlpha = isPlanned ? 0.4 : 1
      ctx.fill()
      ctx.globalAlpha = 1

      // Border
      ctx.strokeStyle = isSelected ? '#ffffff' : isPlanned ? colour : '#0f172a'
      ctx.lineWidth = isSelected ? 3 : isPlanned ? 1.5 : 1
      if (isPlanned) {
        ctx.setLineDash([4, 3])
      }
      ctx.stroke()
      ctx.setLineDash([])

      // Status dot
      const dotRadius = 4
      const dotX = x + radius * 0.65
      const dotY = y - radius * 0.65
      ctx.beginPath()
      ctx.arc(dotX, dotY, dotRadius, 0, 2 * Math.PI)
      ctx.fillStyle = STATUS_INDICATORS[n.status] ?? '#94a3b8'
      ctx.fill()
      ctx.strokeStyle = '#0f172a'
      ctx.lineWidth = 1
      ctx.stroke()

      // Label
      const fontSize = Math.max(11, 13 / globalScale)
      ctx.font = `bold ${fontSize}px Inter, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = '#e2e8f0'
      ctx.fillText(n.label, x, y + radius + 4)

      // Description on hover
      if (isHovered) {
        const descSize = Math.max(9, 10 / globalScale)
        ctx.font = `${descSize}px Inter, system-ui, sans-serif`
        ctx.fillStyle = '#94a3b8'
        ctx.fillText(n.description, x, y + radius + 4 + fontSize + 2)
      }
    },
    [graphData, hoveredNode, selectedNode]
  )

  const linkCanvasObject = useCallback(
    (link: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const start = link.source
      const end = link.target
      if (!start || !end || typeof start.x !== 'number') return

      // Line
      ctx.beginPath()
      ctx.moveTo(start.x, start.y)
      ctx.lineTo(end.x, end.y)
      ctx.strokeStyle = '#334155'
      ctx.lineWidth = 1.5
      ctx.stroke()

      // Arrow
      const angle = Math.atan2(end.y - start.y, end.x - start.x)
      const arrowLen = 8
      const midX = (start.x + end.x) / 2
      const midY = (start.y + end.y) / 2
      ctx.beginPath()
      ctx.moveTo(midX, midY)
      ctx.lineTo(
        midX - arrowLen * Math.cos(angle - Math.PI / 6),
        midY - arrowLen * Math.sin(angle - Math.PI / 6)
      )
      ctx.moveTo(midX, midY)
      ctx.lineTo(
        midX - arrowLen * Math.cos(angle + Math.PI / 6),
        midY - arrowLen * Math.sin(angle + Math.PI / 6)
      )
      ctx.strokeStyle = '#475569'
      ctx.lineWidth = 1.5
      ctx.stroke()

      // Edge label
      if (globalScale > 0.8) {
        const labelSize = Math.max(8, 9 / globalScale)
        ctx.font = `${labelSize}px Inter, system-ui, sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = '#64748b'
        ctx.fillText(link.label ?? '', midX, midY - 8)
      }
    },
    []
  )

  if (!graphData) {
    return (
      <div className="flex items-center justify-center h-[600px] text-slate-400">
        Loading module graph...
      </div>
    )
  }

  const forceData = {
    nodes: graphData.nodes,
    links: graphData.edges.map((e) => ({
      ...e,
      source: e.from,
      target: e.to,
    })),
  }

  return (
    <div className="relative w-full h-[calc(100vh-120px)] bg-slate-950 rounded-xl overflow-hidden">
      {/* Legend */}
      <div className="absolute top-4 left-4 z-10 bg-slate-900/90 backdrop-blur rounded-lg p-3 text-xs space-y-2">
        <div className="text-slate-300 font-medium mb-1">Categories</div>
        {Object.entries(graphData.categories).map(([key, cat]) => (
          <div key={key} className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-full inline-block"
              style={{ backgroundColor: cat.colour }}
            />
            <span className="text-slate-400">{cat.label}</span>
          </div>
        ))}
        <div className="border-t border-slate-700 pt-2 mt-2 text-slate-300 font-medium">Status</div>
        {Object.entries(STATUS_INDICATORS).map(([status, colour]) => (
          <div key={status} className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-full inline-block"
              style={{ backgroundColor: colour }}
            />
            <span className="text-slate-400 capitalize">{status}</span>
          </div>
        ))}
      </div>

      <ForceGraph2D
        ref={graphRef}
        graphData={forceData}
        nodeId="id"
        nodeCanvasObject={nodeCanvasObject}
        nodePointerAreaPaint={(node: any, colour: string, ctx: CanvasRenderingContext2D) => {
          ctx.beginPath()
          ctx.arc(node.x ?? 0, node.y ?? 0, 22, 0, 2 * Math.PI)
          ctx.fillStyle = colour
          ctx.fill()
        }}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        backgroundColor="#020617"
        d3VelocityDecay={0.3}
        d3AlphaDecay={0.02}
        cooldownTicks={100}
        warmupTicks={50}
      />

      {/* Article panel */}
      <ArticlePanel
        node={selectedNode}
        onClose={() => setSelectedNode(null)}
      />
    </div>
  )
}
