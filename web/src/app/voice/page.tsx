'use client'

/**
 * /voice — clean ChatGPT-shaped chat surface with chat-history sidebar.
 *
 * Left rail: "New chat" button + list of past sessions for this user
 *   (most recent first). Mobile: hidden behind a hamburger toggle.
 * Main: scrollable thread + composer at the bottom.
 *
 * Today's brief, when unanswered, surfaces as a banner across the top
 * with a link to /voice/brief. Otherwise the surface is just chat.
 *
 * Auth: 401 → /voice/login.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import Link from 'next/link'
import { Send, LogOut, FileText, Plus, Menu, X } from 'lucide-react'
import { BRAND } from '@/lib/brand'

interface Turn {
  role: 'user' | 'assistant'
  text: string
  at: number
}

interface Me {
  authenticated: boolean
  user?: { email: string; name?: string | null; role: string }
}

interface BriefStatus {
  brief_id: string
  questions_count: number
  answered: boolean
}

interface SessionSummary {
  session_id: string
  title: string
  last_at: string
  turn_count: number
}

export default function VoicePage() {
  const [me, setMe] = useState<Me | null>(null)
  const [transcript, setTranscript] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [partial, setPartial] = useState('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [brief, setBrief] = useState<BriefStatus | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [history, setHistory] = useState<SessionSummary[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ── Boot: auth + brief peek + history ─────────────────────────────
  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const meRes = await fetch('/api/voice/me', { cache: 'no-store' })
        if (meRes.status === 401) {
          window.location.href = '/voice/login?callbackUrl=/voice'
          return
        }
        const meData = (await meRes.json()) as Me
        if (alive) setMe(meData)
      } catch {
        // network — fall through
      }

      // Today's brief (silent if 404)
      try {
        const briefRes = await fetch('/api/deek/brief/today', { cache: 'no-store' })
        if (briefRes.ok) {
          const data = await briefRes.json()
          if (alive && data && !data.answered) {
            setBrief({
              brief_id: data.brief_id,
              questions_count: (data.questions || []).length,
              answered: false,
            })
          }
        }
      } catch {
        // ignore
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  // ── Load chat history when we know who we are ────────────────────
  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/voice/sessions/list?limit=30', {
        cache: 'no-store',
      })
      if (res.ok) {
        const data = await res.json()
        setHistory(data.sessions || [])
      }
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    if (me?.authenticated) {
      loadHistory()
    }
  }, [me, loadHistory])

  // ── Auto-scroll to bottom on new messages ──────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript.length, partial])

  // ── Open a past session ────────────────────────────────────────────
  const openSession = useCallback(async (sid: string) => {
    setSidebarOpen(false)
    setErrorMsg(null)
    setBusy(false)
    setPartial('')
    setSessionId(sid)
    try {
      const res = await fetch(
        `/api/voice/sessions?session_id=${encodeURIComponent(sid)}&limit=100`,
        { cache: 'no-store' },
      )
      if (res.ok) {
        const data = await res.json()
        // Backend returns role='user' | 'deek'; UI uses 'user' | 'assistant'
        const turns: Turn[] = (data.turns || []).map((t: any) => ({
          role: t.role === 'user' ? 'user' : 'assistant',
          text: t.text || '',
          at:
            typeof t.at === 'number'
              ? t.at
              : new Date(t.at).getTime() || Date.now(),
        }))
        setTranscript(turns)
      }
    } catch {
      setErrorMsg("Couldn't load that conversation.")
    }
  }, [])

  // ── New chat ───────────────────────────────────────────────────────
  const newChat = useCallback(() => {
    setSidebarOpen(false)
    abortRef.current?.abort()
    setSessionId(null)
    setTranscript([])
    setPartial('')
    setErrorMsg(null)
    setInput('')
    textareaRef.current?.focus()
  }, [])

  // ── Submit ─────────────────────────────────────────────────────────
  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return
      setTranscript(t => [
        ...t,
        { role: 'user', text: trimmed, at: Date.now() },
      ])
      setInput('')
      setBusy(true)
      setPartial('')
      setErrorMsg(null)

      abortRef.current?.abort()
      abortRef.current = new AbortController()

      try {
        const res = await fetch('/api/voice/chat/agent-stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: trimmed,
            location: 'office',
            session_id: sessionId,
            project: 'deek',
          }),
          signal: abortRef.current.signal,
        })

        if (res.status === 401) {
          window.location.href = '/voice/login?callbackUrl=/voice'
          return
        }
        if (!res.ok || !res.body) {
          setErrorMsg(`Couldn't reach ${BRAND}. HTTP ${res.status}.`)
          setBusy(false)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        let full = ''
        let newSid: string | null = null

        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const blocks = buf.split('\n\n')
          buf = blocks.pop() || ''
          for (const block of blocks) {
            let eventType = 'message'
            let dataStr = ''
            for (const l of block.split('\n')) {
              if (l.startsWith('event: ')) eventType = l.slice(7).trim()
              else if (l.startsWith('data: ')) dataStr = l.slice(6).trim()
            }
            if (!dataStr) continue
            let data: any
            try {
              data = JSON.parse(dataStr)
            } catch {
              continue
            }
            if (eventType === 'response_delta') {
              full += data.text || ''
              setPartial(full)
            } else if (eventType === 'done') {
              if (data.session_id) {
                newSid = data.session_id
                setSessionId(data.session_id)
              }
              setTranscript(t => [
                ...t,
                { role: 'assistant', text: full.trim(), at: Date.now() },
              ])
              setPartial('')
            } else if (eventType === 'error') {
              setErrorMsg(data.error || 'something went wrong')
            }
          }
        }

        // If this was a new chat that just got a session_id, refresh
        // history so the sidebar gains the new entry without a full reload.
        if (newSid) {
          loadHistory()
        }
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          setErrorMsg(err?.message || String(err))
        }
      } finally {
        setBusy(false)
        textareaRef.current?.focus()
      }
    },
    [busy, loadHistory, sessionId],
  )

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    submit(input)
  }

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit(input)
    }
  }

  const handleSignOut = async () => {
    try {
      await fetch('/api/voice/logout', { method: 'POST' })
    } catch {}
    window.location.href = '/voice/login'
  }

  // ── Render ─────────────────────────────────────────────────────────
  if (!me) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-white text-gray-500">
        Loading…
      </div>
    )
  }

  const displayName = me.user?.name || BRAND

  return (
    <div className="flex h-[100dvh] bg-white text-gray-900">
      {/* ── Left sidebar — chat history ────────────────────────────── */}
      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-gray-200 bg-gray-50 transition-transform md:static md:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'
        }`}
      >
        <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-gray-200 px-3 py-2">
          <button
            onClick={newChat}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-900 hover:bg-gray-100"
          >
            <Plus size={14} />
            New chat
          </button>
          <button
            onClick={() => setSidebarOpen(false)}
            className="rounded-md p-1 text-gray-500 hover:bg-gray-200 md:hidden"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          {history.length === 0 && (
            <div className="px-2 py-4 text-xs text-gray-400">
              No past chats yet.
            </div>
          )}
          <ul className="space-y-0.5">
            {history.map(s => {
              const active = s.session_id === sessionId
              return (
                <li key={s.session_id}>
                  <button
                    onClick={() => openSession(s.session_id)}
                    className={`block w-full truncate rounded-md px-2 py-1.5 text-left text-xs ${
                      active
                        ? 'bg-gray-200 text-gray-900'
                        : 'text-gray-700 hover:bg-gray-100'
                    }`}
                    title={s.title}
                  >
                    {s.title}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>

        <div className="flex-shrink-0 border-t border-gray-200 px-3 py-2 text-xs text-gray-500">
          Signed in as {me.user?.email || 'unknown'}
        </div>
      </aside>

      {/* ── Sidebar backdrop on mobile when open ──────────────────── */}
      {sidebarOpen && (
        <div
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-20 bg-black/30 md:hidden"
        />
      )}

      {/* ── Main column ───────────────────────────────────────────── */}
      <div className="flex h-full min-w-0 flex-1 flex-col">
        {/* Top strip */}
        <header className="flex flex-shrink-0 items-center justify-between border-b border-gray-200 px-4 py-2">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSidebarOpen(true)}
              className="rounded-md p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 md:hidden"
              title="Show chat history"
            >
              <Menu size={16} />
            </button>
            <div className="text-sm font-semibold tracking-tight">{BRAND}</div>
          </div>
          <button
            onClick={handleSignOut}
            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-900"
            title="Sign out"
          >
            <LogOut size={12} />
            <span className="hidden sm:inline">Sign out</span>
          </button>
        </header>

        {/* Brief banner */}
        {brief && (
          <Link
            href="/voice/brief"
            className="flex items-center justify-between gap-3 border-b border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800 hover:bg-emerald-100"
          >
            <span className="flex items-center gap-2">
              <FileText size={14} />
              Today&apos;s brief — {brief.questions_count} question
              {brief.questions_count === 1 ? '' : 's'} waiting
            </span>
            <span className="text-xs text-emerald-700">Open →</span>
          </Link>
        )}

        {/* Thread */}
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-2xl space-y-4 px-4 py-6">
            {transcript.length === 0 && !partial && (
              <div className="py-16 text-center">
                <div className="text-2xl font-semibold text-gray-900">
                  Hi {displayName}.
                </div>
                <div className="mt-2 text-sm text-gray-500">
                  Ask {BRAND} anything — type below.
                </div>
              </div>
            )}

            {transcript.map((t, i) => (
              <Bubble key={i} turn={t} />
            ))}

            {partial && (
              <Bubble
                turn={{ role: 'assistant', text: partial, at: Date.now() }}
                streaming
              />
            )}

            {errorMsg && (
              <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
                {errorMsg}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        {/* Composer */}
        <form
          onSubmit={handleSubmit}
          className="flex flex-shrink-0 items-end gap-2 border-t border-gray-200 px-3 py-3"
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            rows={1}
            autoFocus
            disabled={busy}
            placeholder={`Message ${BRAND}…`}
            className="flex-1 resize-none rounded-2xl border border-gray-300 bg-white px-4 py-3 text-base text-gray-900 placeholder-gray-400 focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400 disabled:opacity-50"
            style={{ maxHeight: '10rem' }}
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-900 text-white transition hover:bg-gray-800 disabled:opacity-30"
            title="Send"
          >
            <Send size={18} />
          </button>
        </form>
      </div>
    </div>
  )
}

// ── Speech bubble ─────────────────────────────────────────────────────

function Bubble({
  turn,
  streaming = false,
}: {
  turn: Turn
  streaming?: boolean
}) {
  const isUser = turn.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed ${
          isUser
            ? 'bg-gray-100 text-gray-900'
            : 'bg-white text-gray-900'
        } ${streaming ? 'animate-pulse' : ''}`}
      >
        {turn.text || (streaming ? '…' : '')}
      </div>
    </div>
  )
}
