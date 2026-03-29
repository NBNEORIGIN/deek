'use client'

import type { ReactNode } from 'react'
import { ToolApproval, PendingToolCall } from './ToolApproval'

export interface ToolCallRecord {
  tool_name: string
  result: string
}

export interface MessageMetadata {
  model_routing?: string
  stopped?: boolean
  timed_out?: boolean
  validation_failures?: string[]
  validation_recovered?: boolean
  validation_retries?: number
  memory?: {
    retrieval_mode?: string
    chunks?: number
    files?: number
    mentions?: number
    estimated_tokens?: number
    budget_pct?: number
    exact_hits?: number
    semantic_hits?: number
    both_hits?: number
    retrieved_files?: string[]
    provider?: string
    budget_total?: number
    budget_used?: number
    history_messages?: number
    active_skills?: string[]
    core_tokens?: number
    skill_tokens?: number
    mention_tokens?: number
    retrieved_tokens?: number
  }
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  modelUsed?: string
  costUsd?: number
  modelRouting?: string
  metadata?: MessageMetadata
  pendingToolCall?: PendingToolCall | null
  toolCalls?: ToolCallRecord[]
  activityLog?: unknown[]
  imagePreview?: string
}

interface MessageBubbleProps {
  message: Message
  onApprove?: (toolCall: PendingToolCall) => void
  onReject?: (toolCall: PendingToolCall) => void
}

function renderMarkdown(text: string) {
  const parts: ReactNode[] = []
  let key = 0

  const segments = text.split(/(```[\s\S]*?```)/g)
  for (const seg of segments) {
    if (seg.startsWith('```') && seg.endsWith('```')) {
      const inner = seg.slice(3, -3).replace(/^\w+\n/, '')
      parts.push(
        <pre
          key={key++}
          className="my-3 overflow-x-auto rounded-2xl border border-slate-200 bg-slate-950 px-4 py-3 text-xs leading-5 text-slate-100 shadow-inner"
        >
          {inner}
        </pre>,
      )
    } else {
      const inlineParts = seg.split(/(`[^`]+`)/g)
      for (const ip of inlineParts) {
        if (ip.startsWith('`') && ip.endsWith('`')) {
          parts.push(
            <code
              key={key++}
              className="rounded-md bg-sky-50 px-1.5 py-0.5 text-xs font-medium text-sky-700"
            >
              {ip.slice(1, -1)}
            </code>,
          )
        } else if (ip) {
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
  if (s.includes('qwen') || s.includes('llama') || s.includes('ollama')) return 'Local'
  if (s.includes('deepseek')) return 'DeepSeek'
  if (s.includes('gpt') || s.includes('openai')) return 'OpenAI'
  return 'Claude'
}

function MemoryInfoLine({ metadata, costUsd, modelRouting }: { metadata?: MessageMetadata; costUsd?: number; modelRouting?: string }) {
  const memory = metadata?.memory
  if (!memory) return null

  const provider = memory.provider || 'claude'
  const cost = (costUsd ?? 0) > 0 ? `$${costUsd!.toFixed(4)}` : ''
  const routing = modelRouting === 'manual' ? '[manual]' : '[auto]'
  const chunks = memory.chunks || 0
  const bothHits = memory.both_hits || 0
  const budgetPct = Math.round(memory.budget_pct ?? 0)

  const hitSummary = bothHits > 0
    ? `${chunks} chunks (${bothHits} exact+semantic)`
    : `${chunks} chunks`

  const parts = [provider, cost, routing, '|', hitSummary, `${budgetPct}% budget`].filter(Boolean)

  return (
    <div className="mt-1 text-[11px] text-slate-400">
      {parts.join(' ')}
    </div>
  )
}

function MemorySummary({ metadata }: { metadata?: MessageMetadata }) {
  const memory = metadata?.memory
  if (!memory) return null

  const coreTokens = memory.core_tokens || 0
  const skillTokens = memory.skill_tokens || 0
  const mentionTokens = memory.mention_tokens || 0
  const retrievedTokens = memory.retrieved_tokens || 0
  const historyMessages = memory.history_messages || 0
  const budgetUsed = memory.budget_used || 0
  const budgetTotal = memory.budget_total || 0
  const bothHits = memory.both_hits || 0
  const semanticHits = memory.semantic_hits || 0
  const exactHits = memory.exact_hits || 0
  const chunks = memory.chunks || 0

  return (
    <details className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-slate-50/80">
      <summary className="cursor-pointer list-none px-4 py-3 text-xs text-slate-600">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-700">Memory breakdown</span>
          <span className="ml-auto text-[11px] font-medium text-slate-500">
            {memory.budget_pct ?? 0}% budget
          </span>
        </div>
      </summary>
      <div className="border-t border-slate-200 bg-white px-4 py-3 text-xs text-slate-600">
        <div className="space-y-1.5">
          <div className="flex justify-between">
            <span>Core rules:</span>
            <span className="font-semibold text-slate-800">
              {coreTokens > 0 ? `${coreTokens.toLocaleString()} tokens` : '\u2014'}
              {coreTokens > 0 && <span className="ml-1.5 text-emerald-600">[cached]</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Skill context:</span>
            <span className="font-semibold text-slate-800">
              {skillTokens > 0 ? `${skillTokens.toLocaleString()} tokens` : '\u2014'}
              {skillTokens > 0 && <span className="ml-1.5 text-emerald-600">[cached]</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Recent msgs:</span>
            <span className="font-semibold text-slate-800">
              {historyMessages > 0
                ? `${(memory.estimated_tokens ? memory.estimated_tokens - coreTokens - skillTokens - mentionTokens - retrievedTokens : 0).toLocaleString()} tokens`
                : '\u2014'}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Retrieved:</span>
            <span className="font-semibold text-slate-800">
              {chunks > 0 ? `${chunks} chunks / ${retrievedTokens.toLocaleString()} tokens` : '\u2014'}
            </span>
          </div>
          {chunks > 0 && (
            <div className="ml-4 text-[11px] text-slate-500">
              {bothHits > 0 && <span>{bothHits} exact+semantic</span>}
              {bothHits > 0 && (semanticHits > 0 || exactHits > 0) && <span> &middot; </span>}
              {semanticHits > 0 && <span>{semanticHits} semantic</span>}
              {semanticHits > 0 && exactHits > 0 && <span> &middot; </span>}
              {exactHits > 0 && <span>{exactHits} exact</span>}
            </div>
          )}
          {budgetTotal > 0 && (
            <div className="mt-2 flex justify-between border-t border-slate-100 pt-2">
              <span>Budget:</span>
              <span className="font-semibold text-slate-800">
                {budgetUsed.toLocaleString()} / {budgetTotal.toLocaleString()} tokens ({Math.round(memory.budget_pct ?? 0)}%)
              </span>
            </div>
          )}
        </div>
        {(memory.active_skills || []).length > 0 && (
          <div className="mt-3">
            <div className="mb-1 font-semibold text-slate-700">Active skills</div>
            <div className="flex flex-wrap gap-1.5">
              {memory.active_skills!.map(skill => (
                <span
                  key={skill}
                  className="rounded-full bg-sky-50 px-2.5 py-1 font-medium text-[11px] text-sky-700"
                >
                  {skill}
                </span>
              ))}
            </div>
          </div>
        )}
        {memory.retrieved_files && memory.retrieved_files.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 font-semibold text-slate-700">Top retrieved files</div>
            <div className="flex flex-wrap gap-1.5">
              {memory.retrieved_files.map(file => (
                <span
                  key={file}
                  className="rounded-full bg-slate-100 px-2.5 py-1 font-mono text-[11px] text-slate-600"
                >
                  {file}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </details>
  )
}

function AccomplishmentFooter({ toolCalls, costUsd }: { toolCalls?: ToolCallRecord[]; costUsd?: number }) {
  if (!toolCalls || toolCalls.length === 0) return null

  let filesRead = 0
  let filesEdited = 0
  let testsRun = false
  let testsPassed = 0
  let commitMsg = ''

  for (const tc of toolCalls) {
    if (tc.tool_name === 'read_file') filesRead++
    if (tc.tool_name === 'edit_file' || tc.tool_name === 'create_file') filesEdited++
    if (tc.tool_name === 'run_tests') {
      testsRun = true
      const m = tc.result.match(/(\d+)\s+passed/)
      if (m) testsPassed = parseInt(m[1], 10)
    }
    if (tc.tool_name === 'git_commit') {
      const m = tc.result.match(/\[[\w/-]+\s+[\da-f]+\]\s+(.+)/)
      if (m) commitMsg = m[1]
    }
  }

  const parts: string[] = []
  if (filesRead > 0) parts.push(`${filesRead} file${filesRead > 1 ? 's' : ''} read`)
  if (filesEdited > 0) parts.push(`${filesEdited} file${filesEdited > 1 ? 's' : ''} edited`)
  if (testsRun) parts.push(testsPassed > 0 ? `${testsPassed} tests passed` : 'tests run')
  if (commitMsg) parts.push(`committed: ${commitMsg}`)

  if (parts.length === 0) return null

  return (
    <div className="mt-3 border-t border-slate-100 pt-2 text-[11px] text-slate-400">
      <div>{parts.join(' · ')}</div>
      {(costUsd ?? 0) > 0 && (
        <div className="mt-0.5">${costUsd!.toFixed(4)} total</div>
      )}
    </div>
  )
}

function ValidationBanner({ metadata }: { metadata?: MessageMetadata }) {
  if (!metadata) return null
  if (metadata.stopped) {
    return (
      <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
        Generation stopped before Cairn finished the response.
      </div>
    )
  }
  if (metadata.timed_out) {
    return (
      <div className="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
        Cairn hit the request deadline and returned early.
      </div>
    )
  }
  if (metadata.validation_recovered) {
    return (
      <div className="mt-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-800">
        Validation recovered this answer{metadata.validation_retries ? ` after ${metadata.validation_retries} retry${metadata.validation_retries === 1 ? '' : 'ies'}` : ''}.
      </div>
    )
  }
  return null
}

export function MessageBubble({ message, onApprove, onReject }: MessageBubbleProps) {
  if (message.role === 'user') {
    return (
      <div className="mb-4 flex justify-end">
        <div className="max-w-[78%] rounded-[24px] border border-sky-200 bg-gradient-to-br from-sky-500 to-blue-600 px-4 py-3 text-sm leading-relaxed text-white shadow-[0_12px_30px_-18px_rgba(37,99,235,0.9)]">
          {message.imagePreview && (
            <img
              src={message.imagePreview}
              alt="Attached screenshot"
              className="mb-3 max-h-44 rounded-2xl border border-white/40 shadow-sm"
            />
          )}
          <div className="whitespace-pre-wrap break-words">{message.content}</div>
        </div>
      </div>
    )
  }

  if (message.role === 'system') {
    return (
      <div className="mb-3 flex justify-center">
        <span className="rounded-full border border-slate-200 bg-white/80 px-3 py-1 text-xs text-slate-500 shadow-sm">
          {message.content}
        </span>
      </div>
    )
  }

  return (
    <div className="mb-4">
      <div className="overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-[0_16px_40px_-24px_rgba(15,23,42,0.28)]">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/80 px-4 py-3">
          <span className="rounded-full bg-sky-100 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-sky-700">
            {message.modelUsed ? modelLabel(message.modelUsed) : 'Cairn'}
          </span>
          {message.modelUsed && (
            <span className="text-xs text-slate-500">{message.modelUsed}</span>
          )}
          {(message.costUsd ?? 0) > 0 && (
            <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">
              ${message.costUsd!.toFixed(4)}
            </span>
          )}
          {(message.metadata?.model_routing || message.modelRouting) === 'manual' && (
            <span className="rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700">
              Manual model
            </span>
          )}
          <MemoryInfoLine
            metadata={message.metadata}
            costUsd={message.costUsd}
            modelRouting={message.metadata?.model_routing || message.modelRouting}
          />
        </div>

        <div className="px-5 py-4 text-[15px] leading-7 text-slate-700">
          {renderMarkdown(message.content)}
          <ValidationBanner metadata={message.metadata} />
          <MemorySummary metadata={message.metadata} />
          <AccomplishmentFooter toolCalls={message.toolCalls} costUsd={message.costUsd} />
        </div>
      </div>

      {message.pendingToolCall && !message.pendingToolCall.auto_approve && onApprove && onReject && (
        <ToolApproval
          toolCall={message.pendingToolCall}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}

      {message.toolCalls && message.toolCalls.length > 0 && (
        <details className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-slate-600">
            Tools used · {message.toolCalls.length}
          </summary>
          <div className="space-y-2 border-t border-slate-200 bg-slate-50 p-3">
            {message.toolCalls.map((tc, i) => (
              <div key={i} className="rounded-2xl border border-slate-200 bg-white p-3">
                <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-sky-700">
                  {tc.tool_name}
                </div>
                <div className="whitespace-pre-wrap break-words text-xs leading-6 text-slate-600">
                  {tc.result.length > 400 ? tc.result.slice(0, 400) + '…' : tc.result}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
