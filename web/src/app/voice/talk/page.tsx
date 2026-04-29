'use client'

/**
 * /voice/talk — minimal ChatGPT-like surface.
 *
 * Single column. Scrollable thread. Text input at the bottom. Send.
 * Nothing else on screen. The point is that someone who has used
 * ChatGPT or Claude.ai can sit down here and be productive in zero
 * extra effort.
 *
 * Today's brief, if there's an unanswered one, surfaces as a small
 * banner at the top with a "Open brief" link to /voice/brief. Outside
 * of that, no mode toggles, no location picker, no Eye/Net/Face/Data.
 *
 * Auth: redirects to /voice/login on 401 like every other voice route.
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
import { Send, LogOut, FileText } from 'lucide-react'
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

export default function TalkPage() {
  const [me, setMe] = useState<Me | null>(null)
  const [transcript, setTranscript] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [partial, setPartial] = useState('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [brief, setBrief] = useState<BriefStatus | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ── Boot: auth + brief peek ─────────────────────────────────────────
  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const meRes = await fetch('/api/voice/me', { cache: 'no-store' })
        if (meRes.status === 401) {
          window.location.href = '/voice/login?callbackUrl=/voice/talk'
          return
        }
        const meData = (await meRes.json()) as Me
        if (alive) setMe(meData)
      } catch {
        // network — fall through
      }

      // Peek today's brief — silent if 404
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

  // ── Auto-scroll to bottom on new messages ──────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript.length, partial])

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
            // location is required by the backend but irrelevant for
            // a clean text chat — pin to 'office' so the agent has
            // sensible defaults without surfacing the toggle.
            location: 'office',
            session_id: sessionId,
            project: 'deek',
          }),
          signal: abortRef.current.signal,
        })

        if (res.status === 401) {
          window.location.href = '/voice/login?callbackUrl=/voice/talk'
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
              if (data.session_id) setSessionId(data.session_id)
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
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          setErrorMsg(err?.message || String(err))
        }
      } finally {
        setBusy(false)
        textareaRef.current?.focus()
      }
    },
    [busy, sessionId],
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
      <div className="flex min-h-[100dvh] items-center justify-center bg-slate-950 text-slate-500">
        Loading…
      </div>
    )
  }

  const displayName = me.user?.name || BRAND

  return (
    <div className="flex h-[100dvh] flex-col bg-slate-950 text-slate-100">
      {/* ── Top strip — brand on left, menu on right ─────────────────── */}
      <header className="flex flex-shrink-0 items-center justify-between border-b border-slate-800 px-4 py-2">
        <div className="text-sm font-semibold tracking-tight">{BRAND}</div>
        <button
          onClick={handleSignOut}
          className="flex items-center gap-1 rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:border-slate-500 hover:text-slate-200"
          title="Sign out"
        >
          <LogOut size={12} />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </header>

      {/* ── Brief banner (when there's an unanswered brief) ─────────── */}
      {brief && (
        <Link
          href="/voice/brief"
          className="flex items-center justify-between gap-3 border-b border-emerald-900/40 bg-emerald-950/40 px-4 py-2 text-sm text-emerald-200 hover:bg-emerald-950/60"
        >
          <span className="flex items-center gap-2">
            <FileText size={14} />
            Today&apos;s brief — {brief.questions_count} question
            {brief.questions_count === 1 ? '' : 's'} waiting
          </span>
          <span className="text-xs text-emerald-400">Open →</span>
        </Link>
      )}

      {/* ── Thread ────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl space-y-4 px-4 py-6">
          {transcript.length === 0 && !partial && (
            <div className="py-16 text-center text-slate-500">
              <div className="text-2xl font-semibold text-slate-300">
                Hi {displayName}.
              </div>
              <div className="mt-2 text-sm">
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
            <div className="rounded-md bg-rose-950/60 px-3 py-2 text-sm text-rose-200">
              {errorMsg}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* ── Composer ──────────────────────────────────────────────── */}
      <form
        onSubmit={handleSubmit}
        className="flex flex-shrink-0 items-end gap-2 border-t border-slate-800 px-3 py-3"
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
          className="flex-1 resize-none rounded-2xl border border-slate-700 bg-slate-900 px-4 py-3 text-base placeholder-slate-500 focus:border-emerald-600 focus:outline-none disabled:opacity-50"
          style={{ maxHeight: '10rem' }}
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-600 text-white transition hover:bg-emerald-500 disabled:opacity-40"
          title="Send"
        >
          <Send size={18} />
        </button>
      </form>
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
            ? 'bg-emerald-700/80 text-white'
            : 'bg-slate-900/80 text-slate-100'
        } ${streaming ? 'animate-pulse' : ''}`}
      >
        {turn.text || (streaming ? '…' : '')}
      </div>
    </div>
  )
}
