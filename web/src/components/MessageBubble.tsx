'use client'

import { ToolApproval, PendingToolCall } from './ToolApproval'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  modelUsed?: string
  costUsd?: number
  pendingToolCall?: PendingToolCall | null
}

interface MessageBubbleProps {
  message: Message
  onApprove?: (toolCall: PendingToolCall) => void
  onReject?: (toolCall: PendingToolCall) => void
}

function renderMarkdown(text: string) {
  // Minimal safe markdown rendering
  const parts: React.ReactNode[] = []
  let key = 0

  // Split on fenced code blocks
  const segments = text.split(/(```[\s\S]*?```)/g)
  for (const seg of segments) {
    if (seg.startsWith('```') && seg.endsWith('```')) {
      const inner = seg.slice(3, -3).replace(/^\w+\n/, '')
      parts.push(
        <pre key={key++} className="bg-zinc-900 border border-zinc-700 rounded p-3 overflow-x-auto text-xs font-mono my-2 leading-5">
          {inner}
        </pre>
      )
    } else {
      // Process inline code within the segment
      const inlineParts = seg.split(/(`[^`]+`)/g)
      for (const ip of inlineParts) {
        if (ip.startsWith('`') && ip.endsWith('`')) {
          parts.push(
            <code key={key++} className="bg-zinc-800 rounded px-1 py-0.5 text-xs font-mono text-emerald-300">
              {ip.slice(1, -1)}
            </code>
          )
        } else if (ip) {
          // Render newlines as <br>
          const lines = ip.split('\n')
          lines.forEach((line, i) => {
            if (i > 0) parts.push(<br key={key++} />)
            if (line) parts.push(<span key={key++}>{line}</span>)
          })
        }
      }
    }
  }

  return parts
}

export function MessageBubble({ message, onApprove, onReject }: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[85%] bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-2.5 text-sm text-zinc-100 leading-relaxed">
          {message.content}
        </div>
      </div>
    )
  }

  if (message.role === 'system') {
    return (
      <div className="flex justify-center mb-2">
        <span className="text-xs text-zinc-600">{message.content}</span>
      </div>
    )
  }

  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-xs font-mono text-zinc-500">
          {message.modelUsed
            ? (message.modelUsed.includes('qwen') ? '⚡ local' : '☁ claude')
            : 'CLAW'
          }
        </span>
        {message.costUsd && message.costUsd > 0 && (
          <span className="text-xs text-zinc-600">
            ${message.costUsd.toFixed(4)}
          </span>
        )}
      </div>
      <div className="text-sm text-zinc-200 leading-relaxed">
        {renderMarkdown(message.content)}
      </div>
      {message.pendingToolCall && !message.pendingToolCall.auto_approve && onApprove && onReject && (
        <ToolApproval
          toolCall={message.pendingToolCall}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}
    </div>
  )
}
