'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { MessageBubble, Message } from './MessageBubble'
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
  const [sessionId, setSessionId] = useState<string>(generateId())
  const [sessionCost, setSessionCost] = useState(0)
  const [localCalls, setLocalCalls] = useState(0)
  const [apiCalls, setApiCalls] = useState(0)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

  const newSession = useCallback(() => {
    setSessionId(generateId())
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

    if (content && !toolApproval) {
      setMessages(prev => [...prev, {
        id: generateId(),
        role: 'user',
        content,
      }])
    }

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
        }])
      }
    } catch (err) {
      setMessages(prev => [...prev, {
        id: generateId(),
        role: 'assistant',
        content: `⚠ Network error: ${err}`,
      }])
    } finally {
      setLoading(false)
    }
  }, [projectId, sessionId])

  const handleSubmit = () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
    sendMessage(text)
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
              disabled={!input.trim() || loading || !projectId}
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
