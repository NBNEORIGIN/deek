'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { MessageBubble, Message, ToolCallRecord } from './MessageBubble'
import { PendingToolCall } from './ToolApproval'

interface Project {
  id: string
  name: string
  ready: boolean
}

function generateId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState<string>('')
  const [sessionId, setSessionId] = useState<string>('')
  const [sessionCost, setSessionCost] = useState(0)
  const [localCalls, setLocalCalls] = useState(0)
  const [apiCalls, setApiCalls] = useState(0)
  const [pastedImage, setPastedImage] = useState<string | null>(null)
  const [pastedImageType, setPastedImageType] = useState<string>('image/png')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    // Generate session ID client-side only (avoids SSR hydration mismatch)
    setSessionId(generateId())
  }, [])

  useEffect(() => {
    // Load project list
    fetch('/api/chat')
      .then(r => r.json())
      .then(data => {
        const ready = (data.projects || []).filter((p: Project) => p.ready)
        setProjects(ready)
        if (ready.length > 0) {
          const saved = localStorage.getItem('claw_project')
          const found = ready.find((p: Project) => p.id === saved)
          setProjectId(found ? found.id : ready[0].id)
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (projectId) {
      localStorage.setItem('claw_project', projectId)
      setMessages([{ id: generateId(), role: 'system', content: `Project: ${projectId}` }])
    }
  }, [projectId])

  useEffect(() => {
    const handlePaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items
      if (!items) return
      for (const item of Array.from(items)) {
        if (item.type.startsWith('image/')) {
          e.preventDefault()
          const blob = item.getAsFile()
          if (!blob) return
          const reader = new FileReader()
          reader.onload = (ev) => {
            const result = ev.target?.result as string
            // Strip the data:image/png;base64, prefix — Claude wants raw base64
            const base64 = result.split(',')[1]
            setPastedImage(base64)
            setPastedImageType(['image/jpeg','image/png','image/gif','image/webp'].includes(item.type) ? item.type : 'image/png')
          }
          reader.readAsDataURL(blob)
          break
        }
      }
    }
    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
  }, [])

  const newSession = useCallback(() => {
    setSessionId(generateId()) // safe — always a client click event
    setSessionCost(0)
    setLocalCalls(0)
    setApiCalls(0)
    setMessages([{ id: generateId(), role: 'system', content: `New session — project: ${projectId}` }])
  }, [projectId])

  const sendMessage = useCallback(async (
    content: string,
    toolApproval?: Record<string, unknown>
  ) => {
    if (!projectId) return

    setLoading(true)

    // Capture the image before clearing it
    const imgBase64 = pastedImage
    const imgType = pastedImageType
    const imgPreview = imgBase64 ? `data:${imgType};base64,${imgBase64}` : undefined

    if (content && !toolApproval) {
      setMessages(prev => [...prev, {
        id: generateId(),
        role: 'user',
        content,
        imagePreview: imgPreview,
      }])
    }

    // Clear pasted image after capturing
    setPastedImage(null)

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: content || '',
          project_id: projectId,
          session_id: sessionId,
          channel: 'web',
          tool_approval: toolApproval || null,
          image_base64: imgBase64 || undefined,
          image_media_type: imgType,
        }),
      })

      const data = await res.json()

      if (data.error) {
        setMessages(prev => [...prev, {
          id: generateId(),
          role: 'assistant',
          content: `⚠ ${data.error}`,
        }])
      } else {
        const isLocal = (data.model_used || '').toLowerCase().includes('qwen')
        if (isLocal) {
          setLocalCalls(c => c + 1)
        } else if (data.model_used) {
          setApiCalls(c => c + 1)
          setSessionCost(c => c + (data.cost_usd || 0))
        }

        setMessages(prev => [...prev, {
          id: generateId(),
          role: 'assistant',
          content: data.content || '(no response)',
          modelUsed: data.model_used,
          costUsd: data.cost_usd,
          pendingToolCall: data.pending_tool_call || null,
          toolCalls: (data.tool_calls || []) as ToolCallRecord[],
        }])
      }
    } catch (err) {
      setMessages(prev => {
        // Clear any pending tool call from the last assistant message
        // so the approval card doesn't loop on network failure
        const updated = [...prev]
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].role === 'assistant' && updated[i].pendingToolCall) {
            updated[i] = { ...updated[i], pendingToolCall: null }
            break
          }
        }
        return [...updated, {
          id: generateId(),
          role: 'assistant',
          content: `⚠ Network error — API unreachable. Restart uvicorn and refresh.`,
        }]
      })
    } finally {
      setLoading(false)
    }
  }, [projectId, sessionId, pastedImage, pastedImageType])

  const handleSubmit = () => {
    const text = input.trim()
    if ((!text && !pastedImage) || loading) return
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
    sendMessage(text || 'What do you see in this screenshot?')
  }

  const handleApprove = useCallback((toolCall: PendingToolCall) => {
    sendMessage('', {
      tool_call_id: toolCall.tool_call_id,
      tool_name: toolCall.tool_name,
      input: toolCall.input,
      approved: true,
    })
  }, [sendMessage])

  const handleReject = useCallback((toolCall: PendingToolCall) => {
    sendMessage('', {
      tool_call_id: toolCall.tool_call_id,
      tool_name: toolCall.tool_name,
      input: toolCall.input,
      approved: false,
    })
  }, [sendMessage])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 border-r border-zinc-800 flex flex-col bg-zinc-950">
        <div className="p-4 border-b border-zinc-800">
          <h1 className="text-sm font-bold tracking-widest text-zinc-300 uppercase">CLAW</h1>
          <p className="text-xs text-zinc-600 mt-0.5">Sovereign AI Agent</p>
        </div>

        <div className="p-3 border-b border-zinc-800">
          <label className="text-xs text-zinc-500 uppercase tracking-wide block mb-1.5">Project</label>
          <select
            value={projectId}
            onChange={e => setProjectId(e.target.value)}
            className="w-full bg-zinc-900 border border-zinc-700 text-zinc-200 text-xs rounded px-2 py-1.5"
          >
            {projects.length === 0 && (
              <option value="">No projects</option>
            )}
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.id}</option>
            ))}
          </select>
        </div>

        <div className="p-3 flex flex-col gap-2">
          <button
            onClick={newSession}
            className="w-full text-xs py-1.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors"
          >
            New session
          </button>
        </div>

        <div className="mt-auto p-3 text-xs text-zinc-600 border-t border-zinc-800">
          <div>Cost: ${sessionCost.toFixed(4)}</div>
          <div>Local: {localCalls} · API: {apiCalls}</div>
          <div className="mt-1 text-zinc-700 font-mono text-[10px] truncate">
            {sessionId.slice(0, 12)}…
          </div>
        </div>
      </aside>

      {/* Main chat */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onApprove={handleApprove}
              onReject={handleReject}
            />
          ))}

          {loading && (
            <div className="text-sm text-zinc-500 italic animate-pulse mb-3">
              Thinking…
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="border-t border-zinc-800 bg-zinc-950 px-4 py-3">
          {pastedImage && (
            <div className="relative mb-2 inline-block">
              <img
                src={`data:${pastedImageType};base64,${pastedImage}`}
                alt="Pasted screenshot"
                className="max-h-28 rounded border border-zinc-600"
              />
              <button
                onClick={() => setPastedImage(null)}
                className="absolute -top-2 -right-2 bg-zinc-700 hover:bg-zinc-600 rounded-full w-5 h-5 text-xs flex items-center justify-center text-zinc-300 leading-none"
              >×</button>
            </div>
          )}
          <div className="flex gap-3 items-end">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder={projectId ? `Ask CLAW about ${projectId}…` : 'Select a project first'}
              disabled={!projectId || loading}
              rows={1}
              className="flex-1 bg-zinc-900 border border-zinc-700 text-zinc-100 text-sm rounded-lg px-3 py-2 resize-none min-h-[38px] max-h-[120px] focus:outline-none focus:border-zinc-500 placeholder:text-zinc-600 disabled:opacity-50"
            />
            <button
              onClick={handleSubmit}
              disabled={(!input.trim() && !pastedImage) || loading || !projectId}
              className="px-4 py-2 bg-zinc-700 hover:bg-zinc-600 disabled:opacity-40 text-white text-sm rounded-lg transition-colors"
            >
              Send
            </button>
          </div>
          <p className="text-xs text-zinc-700 mt-1.5">
            Enter to send · Shift+Enter for newline
          </p>
        </div>
      </div>
    </div>
  )
}
