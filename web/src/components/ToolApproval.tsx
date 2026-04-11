'use client'

import { BookOpen, Wrench, AlertTriangle } from 'lucide-react'
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

const RISK_STYLES: Record<PendingToolCall['risk_level'], string> = {
  safe:        'border-slate-200 bg-white',
  review:      'border-amber-200 bg-amber-50',
  destructive: 'border-red-200 bg-red-50',
}

const RISK_ICONS: Record<PendingToolCall['risk_level'], typeof BookOpen> = {
  safe:        BookOpen,
  review:      Wrench,
  destructive: AlertTriangle,
}

const RISK_LABEL_COLOR: Record<PendingToolCall['risk_level'], string> = {
  safe:        'text-slate-600',
  review:      'text-amber-700',
  destructive: 'text-red-700',
}

export function ToolApproval({ toolCall, onApprove, onReject }: ToolApprovalProps) {
  const riskStyle = RISK_STYLES[toolCall.risk_level] || RISK_STYLES.review
  const Icon = RISK_ICONS[toolCall.risk_level] || Wrench
  const labelColor = RISK_LABEL_COLOR[toolCall.risk_level] || 'text-slate-600'

  return (
    <div className={`mt-3 rounded-md border p-3.5 ${riskStyle}`}>
      <div className="mb-2 flex items-center gap-2">
        <Icon size={14} className={`shrink-0 ${labelColor}`} />
        <span className="font-mono text-xs font-medium text-slate-900">{toolCall.tool_name}</span>
        <span className={`ml-auto text-2xs font-medium uppercase tracking-wider ${labelColor}`}>
          {toolCall.risk_level}
        </span>
      </div>

      <p className="mb-3 text-2xs leading-relaxed text-slate-600">{toolCall.description}</p>

      {toolCall.diff_preview && (
        <div className="mb-3">
          <DiffViewer diff={toolCall.diff_preview} />
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => onApprove(toolCall)}
          className="focus-ring rounded-md bg-slate-900 px-3.5 py-1.5 text-2xs font-medium text-white transition-colors hover:bg-slate-700"
        >
          Apply
        </button>
        <button
          onClick={() => onReject(toolCall)}
          className="focus-ring rounded-md border border-slate-200 bg-white px-3.5 py-1.5 text-2xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
        >
          Reject
        </button>
      </div>
    </div>
  )
}
