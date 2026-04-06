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
  production: '#16a34a',
  development: '#d97706',
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

      const radius = isHovered || isSelected ? 26 : 22
      const x = node.x ?? 0
      const y = node.y ?? 0

      // Shadow
      if (!isPlanned) {
        ctx.beginPath()
        ctx.arc(x + 1, y + 2, radius, 0, 2 * Math.PI)
        ctx.fillStyle = 'rgba(0, 0, 0, 0.08)'
        ctx.fill()
      }

      // Node circle — white fill with coloured border
      ctx.beginPath()
      ctx.arc(x, y, radius, 0, 2 * Math.PI)
      ctx.fillStyle = isPlanned ? '#f8fafc' : '#ffffff'
      ctx.globalAlpha = isPlanned ? 0.6 : 1
      ctx.fill()
      ctx.globalAlpha = 1

      // Coloured border
      ctx.strokeStyle = isSelected ? '#4f46e5' : colour
      ctx.lineWidth = isSelected ? 3.5 : isHovered ? 3 : 2.5
      if (isPlanned) {
        ctx.setLineDash([5, 4])
        ctx.lineWidth = 2
      }
      ctx.stroke()
      ctx.setLineDash([])

      // Category colour dot (top-left)
      const dotRadius = 4.5
      const dotX = x - radius * 0.55
      const dotY = y - radius * 0.55
      ctx.beginPath()
      ctx.arc(dotX, dotY, dotRadius, 0, 2 * Math.PI)
      ctx.fillStyle = colour
      ctx.fill()

      // Status dot (top-right)
      const statusX = x + radius * 0.55
      const statusY = y - radius * 0.55
      ctx.beginPath()
      ctx.arc(statusX, statusY, 3.5, 0, 2 * Math.PI)
      ctx.fillStyle = STATUS_INDICATORS[n.status] ?? '#94a3b8'
      ctx.fill()
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 1.5
      ctx.stroke()

      // Label — inside the node
      const fontSize = Math.max(9, 11 / globalScale)
      ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = '#1e293b'

      // Wrap long labels
      const label = n.label
      if (label.length > 12) {
        const words = label.split(' ')
        if (words.length >= 2) {
          const mid = Math.ceil(words.length / 2)
          const line1 = words.slice(0, mid).join(' ')
          const line2 = words.slice(mid).join(' ')
          ctx.fillText(line1, x, y - fontSize * 0.4)
          ctx.fillText(line2, x, y + fontSize * 0.6)
        } else {
          ctx.fillText(label, x, y)
        }
      } else {
        ctx.fillText(label, x, y)
      }

      // Description below node on hover
      if (isHovered) {
        const descSize = Math.max(8, 9 / globalScale)
        ctx.font = `${descSize}px Inter, system-ui, sans-serif`
        ctx.fillStyle = '#64748b'
        ctx.textBaseline = 'top'
        ctx.fillText(n.description, x, y + radius + 6)
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
      ctx.strokeStyle = '#cbd5e1'
      ctx.lineWidth = 1.2
      ctx.stroke()

      // Arrow at midpoint
      const angle = Math.atan2(end.y - start.y, end.x - start.x)
      const arrowLen = 7
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
      ctx.strokeStyle = '#94a3b8'
      ctx.lineWidth = 1.2
      ctx.stroke()

      // Edge label
      if (globalScale > 0.7) {
        const labelSize = Math.max(7, 8 / globalScale)
        ctx.font = `${labelSize}px Inter, system-ui, sans-serif`
        ctx.textAlign = 'center'
        ctx.textBaseline = 'middle'

        // Background pill for readability
        const text = link.label ?? ''
        if (text) {
          const metrics = ctx.measureText(text)
          const pw = metrics.width + 6
          const ph = labelSize + 4
          ctx.fillStyle = '#ffffff'
          ctx.globalAlpha = 0.85
          ctx.fillRect(midX - pw / 2, midY - 8 - ph / 2, pw, ph)
          ctx.globalAlpha = 1

          ctx.fillStyle = '#64748b'
          ctx.fillText(text, midX, midY - 8)
        }
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
    <div className="relative w-full h-[calc(100vh-120px)] bg-slate-50 rounded-xl border border-slate-200 overflow-hidden">
      {/* Legend */}
      <div className="absolute top-4 left-4 z-10 bg-white/90 backdrop-blur border border-slate-200 rounded-lg p-3 text-xs space-y-1.5 shadow-sm">
        <div className="text-slate-700 font-semibold mb-1">Categories</div>
        {Object.entries(graphData.categories).map(([key, cat]) => (
          <div key={key} className="flex items-center gap-2">
            <span
              className="w-3 h-3 rounded-full inline-block"
              style={{ backgroundColor: cat.colour }}
            />
            <span className="text-slate-600">{cat.label}</span>
          </div>
        ))}
        <div className="border-t border-slate-200 pt-1.5 mt-1.5 text-slate-700 font-semibold">Status</div>
        {Object.entries(STATUS_INDICATORS).map(([status, colour]) => (
          <div key={status} className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-full inline-block"
              style={{ backgroundColor: colour }}
            />
            <span className="text-slate-600 capitalize">{status}</span>
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
          ctx.arc(node.x ?? 0, node.y ?? 0, 26, 0, 2 * Math.PI)
          ctx.fillStyle = colour
          ctx.fill()
        }}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        onNodeHover={handleNodeHover}
        backgroundColor="#f8fafc"
        d3VelocityDecay={0.25}
        d3AlphaDecay={0.015}
        cooldownTicks={150}
        warmupTicks={80}
        d3AlphaMin={0.005}
        linkDistance={180}
        dagLevelDistance={120}
        onEngineStop={() => {
          if (graphRef.current) {
            graphRef.current.zoomToFit(400, 60)
          }
        }}
        onEngineTick={() => {
          // Increase charge repulsion on first render
          const fg = graphRef.current
          if (fg) {
            const charge = fg.d3Force('charge')
            if (charge && !charge._configured) {
              charge.strength(-600).distanceMax(500)
              charge._configured = true
            }
            const link = fg.d3Force('link')
            if (link && !link._configured) {
              link.distance(180)
              link._configured = true
            }
          }
        }}
      />

      {/* Article panel */}
      <ArticlePanel
        node={selectedNode}
        onClose={() => setSelectedNode(null)}
      />
    </div>
  )
}
