'use client'

import { DiffViewer } from './DiffViewer'

export interface PendingToolCall {
  tool_call_id: string
  tool_name: string
  description: string
  diff_preview: string
  input: Record<string, unknown>
  risk_level: 'safe' | 'review' | 'destructive'
  auto_approve: boolean
}

interface ToolApprovalProps {
  toolCall: PendingToolCall
  onApprove: (toolCall: PendingToolCall) => void
  onReject: (toolCall: PendingToolCall) => void
}

const RISK_STYLES = {
  safe:        'border-zinc-600 bg-zinc-900',
  review:      'border-amber-600 bg-amber-950/30',
  destructive: 'border-red-600 bg-red-950/30',
}

const RISK_ICONS = {
  safe:        '📖',
  review:      '🔧',
  destructive: '⚠️',
}

export function ToolApproval({ toolCall, onApprove, onReject }: ToolApprovalProps) {
  const riskStyle = RISK_STYLES[toolCall.risk_level] || RISK_STYLES.review
  const riskIcon  = RISK_ICONS[toolCall.risk_level]  || '🔧'

  return (
    <div className={`border rounded-lg p-3 mt-2 ${riskStyle}`}>
      <div className="flex items-center gap-2 font-semibold text-sm mb-1">
        <span>{riskIcon}</span>
        <span className="text-zinc-200">{toolCall.tool_name}</span>
        <span className="ml-auto text-xs text-zinc-500 uppercase tracking-wide">
          {toolCall.risk_level}
        </span>
      </div>

      <p className="text-xs text-zinc-400 mb-3">{toolCall.description}</p>

      {toolCall.diff_preview && (
        <div className="mb-3">
          <DiffViewer diff={toolCall.diff_preview} />
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => onApprove(toolCall)}
          className="px-3 py-1 text-xs rounded bg-emerald-700 hover:bg-emerald-600 text-white transition-colors"
        >
          Apply
        </button>
        <button
          onClick={() => onReject(toolCall)}
          className="px-3 py-1 text-xs rounded bg-zinc-700 hover:bg-zinc-600 text-zinc-200 transition-colors"
        >
          Reject
        </button>
      </div>
    </div>
  )
}
