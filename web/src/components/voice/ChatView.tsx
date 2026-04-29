'use client'

/**
 * ChatView — ChatGPT-like text interface for Deek.
 *
 * Routes to /api/voice/chat/agent-stream — the FULL agent pipeline with
 * tools (query_amazon_intel, search_crm, search_wiki, etc). Voice mode
 * still uses /api/voice/chat/stream (tool-less, TTS-optimised) for
 * latency + TTS word budget reasons.
 * - Input is a textarea, not a microphone
 * - Output is rendered as text, not spoken
 * - No turn-taking state machine — one question → one streamed response
 *
 * Shares `transcript` state with VoiceView so switching modes doesn't
 * lose conversation history.
 */
import { useCallback, useEffect, useRef, useState, FormEvent, KeyboardEvent } from 'react'
import type { VoiceLoopTurn } from '@/hooks/useVoiceLoop'
import type { Location } from './types'
import { BRAND } from '@/lib/brand'

export function ChatView({
  location,
  transcript,
  onTurn,
  sessionId,
  onSessionId,
}: {
  location: Location
  transcript: VoiceLoopTurn[]
  onTurn: (t: VoiceLoopTurn) => void
  sessionId: string | null
  onSessionId: (id: string) => void
}) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [partial, setPartial] = useState('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript.length, partial])

  const submit = useCallback(
    async (text: string) => {
      if (!text.trim() || busy) return
      const userText = text.trim()
      onTurn({ role: 'user', text: userText, at: Date.now() })
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
            content: userText,
            location,
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
          setErrorMsg(`Stream failed HTTP ${res.status}`)
          setBusy(false)
          return
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        let fullText = ''

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
              fullText += data.text || ''
              setPartial(fullText)
            } else if (eventType === 'done') {
              if (data.session_id) onSessionId(data.session_id)
              onTurn({
                role: 'deek',
                text: fullText.trim(),
                at: Date.now(),
                outcome: data.outcome,
              })
              setPartial('')
            } else if (eventType === 'error') {
              setErrorMsg(data.error || 'stream error')
            }
          }
        }
      } catch (err: any) {
        if (err?.name !== 'AbortError') {
          setErrorMsg(err?.message || String(err))
        }
      } finally {
        setBusy(false)
      }
    },
    [busy, location, onSessionId, onTurn, sessionId],
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

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {transcript.length === 0 && !partial && (
          <div className="py-12 text-center text-slate-600">
            Ask {BRAND} anything. Responses stream in real time.
          </div>
        )}
        {transcript.map((m, i) => (
          <MessageBubble key={i} turn={m} />
        ))}
        {partial && (
          <MessageBubble
            turn={{ role: 'deek', text: partial, at: Date.now() }}
            streaming
          />
        )}
        <div ref={bottomRef} />
      </div>

      {errorMsg && (
        <div className="mx-4 mb-2 rounded-lg bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
          {errorMsg}
        </div>
      )}

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="flex items-end gap-2 border-t border-slate-800 p-3"
      >
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          rows={1}
          disabled={busy}
          placeholder={`Message ${BRAND}…`}
          className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-base placeholder-slate-500 focus:border-emerald-500 focus:outline-none disabled:opacity-50"
          style={{ maxHeight: '8rem' }}
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? '…' : 'Send'}
        </button>
      </form>
    </div>
  )
}

function MessageBubble({
  turn,
  streaming,
}: {
  turn: VoiceLoopTurn
  streaming?: boolean
}) {
  const isUser = turn.role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm ${
          isUser
            ? 'bg-emerald-700 text-white'
            : turn.outcome === 'backend_error'
            ? 'bg-rose-950/60 text-rose-200'
            : 'bg-slate-800 text-slate-100'
        } ${streaming ? 'animate-pulse' : ''}`}
        style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
      >
        {turn.text || (streaming ? '…' : '')}
      </div>
    </div>
  )
}
