'use client'

import { ToolApproval, PendingToolCall } from './ToolApproval'

export interface ToolCallRecord {
  tool_name: string
  result: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  modelUsed?: string
  costUsd?: number
  modelRouting?: string    // 'auto' | 'manual'
  pendingToolCall?: PendingToolCall | null
  toolCalls?: ToolCallRecord[]
  activityLog?: unknown[]  // ActivityEvent[] — rendered by ChatWindow's ActivityLog
  imagePreview?: string    // data URL for pasted image thumbnail
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

function modelLabel(m: string): string {
  const s = m.toLowerCase()
  if (s.includes('qwen') || s.includes('llama') || s.includes('ollama')) return '⚡ local'
  if (s.includes('deepseek')) return '🌊 deepseek'
  if (s.includes('gpt') || s.includes('openai')) return '🤖 openai'
  return '☁ claude'
}

export function MessageBubble({ message, onApprove, onReject }: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end mb-3">
        <div className="max-w-[85%] bg-zinc-800 border border-zinc-700 rounded-xl px-4 py-2.5 text-sm text-zinc-100 leading-relaxed">
          {message.imagePreview && (
            <img
              src={message.imagePreview}
              alt="Attached screenshot"
              className="max-h-40 rounded mb-2 border border-zinc-600"
            />
          )}
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
            ? modelLabel(message.modelUsed)
            : 'CLAW'
          }
        </span>
        {(message.costUsd ?? 0) > 0 && (
          <span className="text-xs text-zinc-600">
            ${message.costUsd!.toFixed(4)}
          </span>
        )}
        {message.modelRouting === 'manual' && (
          <span className="text-[10px] text-amber-600 border border-amber-800 rounded px-1 py-0.5 font-mono">
            manual
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
      {message.toolCalls && message.toolCalls.length > 0 && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-zinc-500 hover:text-zinc-400 select-none">
            🔧 {message.toolCalls.length} tool call{message.toolCalls.length > 1 ? 's' : ''}
          </summary>
          <div className="mt-2 space-y-2">
            {message.toolCalls.map((tc, i) => (
              <div key={i} className="bg-zinc-900 border border-zinc-800 rounded p-2">
                <div className="font-mono text-blue-400 mb-1">{tc.tool_name}</div>
                <div className="text-zinc-400 whitespace-pre-wrap break-words">
                  {tc.result.length > 400
                    ? tc.result.slice(0, 400) + '…'
                    : tc.result}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
