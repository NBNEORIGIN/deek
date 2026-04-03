'use client'

import { useEffect, useRef, useState, useCallback, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  isError?: boolean
  saved?: boolean
}

// ---- Markdown renderer -------------------------------------------------------

function renderMarkdown(text: string): string {
  const lines = text.split('\n')
  const output: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    if (line.startsWith('```')) {
      const langMatch = line.match(/^```(\w*)/)
      const lang = langMatch?.[1] ?? ''
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(
          lines[i]
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
        )
        i++
      }
      i++ // skip closing ```
      const langAttr = lang ? ` class="language-${lang}"` : ''
      output.push(
        `<pre class="bg-slate-100 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code${langAttr}>${codeLines.join('\n')}</code></pre>`
      )
      continue
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim())) {
      output.push('<hr class="border-slate-200 my-3" />')
      i++
      continue
    }

    // Headings
    if (line.startsWith('### ')) {
      output.push(`<h3 class="text-base font-semibold text-slate-900 mt-4 mb-1">${inlineMarkdown(line.slice(4))}</h3>`)
      i++
      continue
    }
    if (line.startsWith('## ')) {
      output.push(`<h2 class="text-lg font-semibold text-slate-900 mt-4 mb-1">${inlineMarkdown(line.slice(3))}</h2>`)
      i++
      continue
    }
    if (line.startsWith('# ')) {
      output.push(`<h1 class="text-xl font-bold text-slate-900 mt-4 mb-1">${inlineMarkdown(line.slice(2))}</h1>`)
      i++
      continue
    }

    // Table: detect line starting with | and next line contains |---|
    if (line.trim().startsWith('|')) {
      const tableLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i])
        i++
      }
      // Find separator row
      const sepIdx = tableLines.findIndex((l) => /^\|[\s\-:|]+\|/.test(l))
      if (sepIdx === 1 && tableLines.length >= 2) {
        const headerCells = splitTableRow(tableLines[0])
        const bodyRows = tableLines.slice(2)
        const headerHtml = headerCells
          .map((c) => `<th class="px-3 py-2 text-left text-xs font-semibold text-slate-700 bg-slate-100 border border-slate-200">${inlineMarkdown(c)}</th>`)
          .join('')
        const bodyHtml = bodyRows
          .map((row) => {
            const cells = splitTableRow(row)
            return (
              '<tr class="even:bg-slate-50">' +
              cells
                .map((c) => `<td class="px-3 py-2 text-xs text-slate-700 border border-slate-200">${inlineMarkdown(c)}</td>`)
                .join('') +
              '</tr>'
            )
          })
          .join('')
        output.push(
          `<div class="overflow-x-auto my-3"><table class="w-full border-collapse text-sm"><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`
        )
      } else {
        // Not a valid table — just render as lines
        tableLines.forEach((tl) => output.push(`<p>${inlineMarkdown(tl)}</p>`))
      }
      continue
    }

    // List items
    if (/^[-*]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s/.test(lines[i])) {
        items.push(`<li class="text-slate-700">${inlineMarkdown(lines[i].slice(2))}</li>`)
        i++
      }
      output.push(`<ul class="list-disc list-inside space-y-1 my-2">${items.join('')}</ul>`)
      continue
    }

    // Numbered list items
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(`<li class="text-slate-700">${inlineMarkdown(lines[i].replace(/^\d+\.\s/, ''))}</li>`)
        i++
      }
      output.push(`<ol class="list-decimal list-inside space-y-1 my-2">${items.join('')}</ol>`)
      continue
    }

    // Blank line
    if (line.trim() === '') {
      i++
      continue
    }

    // Normal paragraph
    output.push(`<p class="text-slate-800 my-1">${inlineMarkdown(line)}</p>`)
    i++
  }

  return output.join('')
}

function splitTableRow(row: string): string[] {
  return row
    .split('|')
    .slice(1, -1)
    .map((c) => c.trim())
}

function inlineMarkdown(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/`([^`]+)`/g, '<code class="bg-slate-100 rounded px-1 text-xs font-mono">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
}

function AssistantBubble({ content, isError }: { content: string; isError?: boolean }) {
  if (isError) {
    return (
      <p className="text-sm text-red-600">{content}</p>
    )
  }
  return (
    <div
      className="text-sm [&_p]:leading-relaxed [&_ul]:my-1 [&_ol]:my-1"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
    />
  )
}

// ---- Session ID --------------------------------------------------------------

function generateSessionId() {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

// ---- Inner component (uses useSearchParams) ----------------------------------

function AskPageInner() {
  const searchParams = useSearchParams()
  const prefill = searchParams.get('q') ?? ''

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(prefill)
  const [sending, setSending] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const sessionId = useRef<string>(generateSessionId())
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)

  const saveToMemory = useCallback(async (msgId: string) => {
    // Find the assistant message and the preceding user message for context
    const idx = messages.findIndex((m) => m.id === msgId)
    if (idx < 0) return
    const assistantMsg = messages[idx]
    const userMsg = idx > 0 ? messages[idx - 1] : null
    const query = userMsg?.content || 'Chat exchange'

    setSaving(msgId)
    try {
      const res = await fetch('/api/notes/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: `Remembered: ${query.slice(0, 80)}`,
          content: `**Question:** ${query}\n\n**Answer:** ${assistantMsg.content}`,
        }),
      })
      if (res.ok) {
        setMessages((prev) =>
          prev.map((m) => (m.id === msgId ? { ...m, saved: true } : m))
        )
      }
    } catch {
      // silently fail
    } finally {
      setSaving(null)
    }
  }, [messages])

  // Auto-scroll to bottom whenever messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || sending) return

    const userMsg: Message = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
    }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setSending(true)

    // Close any existing EventSource
    if (esRef.current) {
      esRef.current.close()
      esRef.current = null
    }

    // Create placeholder assistant message
    const assistantId = `a-${Date.now()}`
    setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '' }])

    // Build SSE URL
    const params = new URLSearchParams({
      project: 'nbne',
      session_id: sessionId.current,
      message: text,
      channel: 'web',
    })

    const es = new EventSource(`/api/chat/stream?${params.toString()}`)
    esRef.current = es

    es.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data)
        const type: string = parsed.type ?? ''

        if (type === 'response_delta') {
          // Streamed text chunk — append to message
          const chunk: string = parsed.text ?? ''
          if (chunk) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + chunk } : m
              )
            )
          }
        } else if (type === 'complete') {
          // Final response — use if no delta text received yet
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m
              if (m.content) return m // deltas already built the message
              return { ...m, content: parsed.response ?? '' }
            })
          )
          es.close()
          esRef.current = null
          setSending(false)
        } else if (type === 'error') {
          const errMsg: string = parsed.message ?? parsed.error ?? 'An error occurred'
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: errMsg, isError: true }
                : m
            )
          )
          es.close()
          esRef.current = null
          setSending(false)
        } else if (type === 'done') {
          es.close()
          esRef.current = null
          setSending(false)
        }
        // Ignore: status, routing, tokens, tool_start, tool_end, tool_queued
      } catch {
        // Non-JSON or unparseable — ignore
      }
    }

    es.onerror = () => {
      es.close()
      esRef.current = null
      setSending(false)
    }
  }, [input, sending])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-56px-3rem)] max-w-3xl mx-auto">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-slate-400">
              Ask anything about the business — stock, orders, processes, financials.
            </p>
          </div>
        )}

        {messages.map((msg) =>
          msg.role === 'user' ? (
            <div key={msg.id} className="flex justify-end">
              <div className="max-w-[75%] bg-indigo-600 text-white text-sm px-4 py-3 rounded-2xl rounded-tr-sm">
                {msg.content}
              </div>
            </div>
          ) : (
            <div key={msg.id} className="flex justify-start">
              <div className="max-w-[75%] bg-white border border-slate-200 text-slate-800 text-sm px-4 py-3 rounded-2xl rounded-tl-sm shadow-sm">
                {msg.content ? (
                  <>
                    <AssistantBubble content={msg.content} isError={msg.isError} />
                    {!msg.isError && !sending && (
                      <div className="mt-2 pt-2 border-t border-slate-100">
                        {msg.saved ? (
                          <span className="text-xs text-emerald-600">✓ Saved to memory</span>
                        ) : (
                          <button
                            onClick={() => saveToMemory(msg.id)}
                            disabled={saving === msg.id}
                            className="text-xs text-slate-400 hover:text-indigo-600 transition-colors disabled:opacity-50"
                          >
                            {saving === msg.id ? 'Saving…' : '💾 Remember this'}
                          </button>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <span className="text-slate-400 animate-pulse">Thinking…</span>
                )}
              </div>
            </div>
          )
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="bg-white border border-slate-200 rounded-xl p-3 flex gap-3 items-end shadow-sm">
        <textarea
          className="flex-1 resize-none text-sm text-slate-800 placeholder-slate-400 focus:outline-none min-h-[40px] max-h-[160px] overflow-y-auto"
          rows={1}
          placeholder="Ask anything…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={sending}
        />
        <button
          onClick={sendMessage}
          disabled={sending || !input.trim()}
          className="flex-shrink-0 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
    </div>
  )
}

// ---- Page export -------------------------------------------------------------

export default function AskPage() {
  return (
    <Suspense fallback={<div className="text-sm text-slate-400">Loading…</div>}>
      <AskPageInner />
    </Suspense>
  )
}
