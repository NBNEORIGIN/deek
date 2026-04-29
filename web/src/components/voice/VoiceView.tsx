'use client'

/**
 * VoiceView — split-screen voice interface.
 *
 * Top half: Eye | Data toggle
 *   Eye  → HAL 9000 animation reacting to mic + TTS state
 *   Data → Live ambient panels (same data as the old /voice page)
 *
 * Bottom half: live transcript (newest last, auto-scroll) + big start/stop
 * button. Continuous voice mode by default — auto turn-taking.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { HalEye, EyeState } from './HalEye'
import { DeekConstellation } from './DeekConstellation'
import { useVoiceLoop, VoiceLoopTurn } from '@/hooks/useVoiceLoop'
import type { Location } from './types'
import { BRAND } from '@/lib/brand'

const FACE_KEY = 'deek.face'
type FaceChoice = 'eye' | 'net'

interface AmbientPanel {
  id: string
  title: string
  items: { label: string; status?: string | null; detail?: string | null }[]
}
interface AmbientPayload {
  location: Location
  morning_number: {
    number: string
    unit: string
    headline: string
    subtitle: string
    source_module: string
    stale: boolean
  }
  panels: AmbientPanel[]
  offline?: boolean
}

export function VoiceView({
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
  const [topView, setTopView] = useState<'face' | 'data'>('face')
  const [face, setFace] = useState<FaceChoice>('net')  // Deek's own choice wins by default
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const transcriptEndRef = useRef<HTMLDivElement>(null)

  // Restore saved face preference
  useEffect(() => {
    const saved = localStorage.getItem(FACE_KEY) as FaceChoice | null
    if (saved === 'eye' || saved === 'net') setFace(saved)
  }, [])
  const pickFace = useCallback((f: FaceChoice) => {
    setFace(f)
    try { localStorage.setItem(FACE_KEY, f) } catch {}
  }, [])

  const {
    state,
    running,
    interim,
    partialResponse,
    soundDetected,
    start,
    stop,
  } = useVoiceLoop({
    location,
    sessionId,
    onTurn,
    onSessionId,
    onError: msg => setErrorMsg(msg),
  })

  // Use Web Speech's own sound-detection events to drive the eye
  // (avoids a conflicting getUserMedia stream). When sound is detected
  // we boost the amplitude; otherwise fall back to a gentle idle hint.
  const amp = soundDetected ? 0.6 : 0

  const eyeState: EyeState = state

  // Auto-scroll transcript to newest
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript.length, partialResponse])

  // Ambient data for Data view
  const [ambient, setAmbient] = useState<AmbientPayload | null>(null)
  useEffect(() => {
    if (topView !== 'data') return
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(`/api/voice/ambient?location=${location}`, {
          cache: 'no-store',
        })
        if (res.ok && !cancelled) setAmbient(await res.json())
      } catch {}
    }
    load()
    const id = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [topView, location])

  const toggleRunning = useCallback(() => {
    setErrorMsg(null)
    if (running) {
      stop()
    } else {
      start()
    }
  }, [running, start, stop])

  const statusLabel = useMemo(() => {
    if (!running) return 'Tap the eye to start'
    if (state === 'listening') return interim || 'Listening…'
    if (state === 'thinking') return 'Thinking…'
    if (state === 'speaking') return 'Speaking…'
    return ''
  }, [running, state, interim])

  return (
    <div className="flex h-full flex-col bg-slate-950 text-slate-100">
      {/* ── Top half ─────────────────────────────────────────────────── */}
      <div className="relative flex h-1/2 min-h-0 items-center justify-center border-b border-slate-800">
        {/* Top-half view toggle: Face | Data */}
        <div className="absolute left-4 top-4 z-10 flex overflow-hidden rounded-full border border-slate-700 text-xs">
          <button
            onClick={() => setTopView('face')}
            className={`px-3 py-1 ${topView === 'face' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
          >
            Face
          </button>
          <button
            onClick={() => setTopView('data')}
            className={`px-3 py-1 ${topView === 'data' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
          >
            Data
          </button>
        </div>

        {/* Face sub-toggle: Eye | Net — only visible when face is showing */}
        {topView === 'face' && (
          <div className="absolute right-4 top-4 z-10 flex overflow-hidden rounded-full border border-slate-700 text-xs">
            <button
              onClick={() => pickFace('eye')}
              title="HAL-style red eye"
              className={`px-3 py-1 ${face === 'eye' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
            >
              Eye
            </button>
            <button
              onClick={() => pickFace('net')}
              title="Constellation — Deek's self-designed face"
              className={`px-3 py-1 ${face === 'net' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
            >
              Net
            </button>
          </div>
        )}

        {topView === 'face' ? (
          <div className="flex flex-col items-center gap-6">
            {face === 'eye' ? (
              <HalEye
                state={eyeState}
                amplitude={amp}
                size={280}
                onClick={toggleRunning}
              />
            ) : (
              <DeekConstellation
                state={eyeState}
                soundIntensity={amp}
                size={280}
                onClick={toggleRunning}
              />
            )}
            <div
              className={`text-sm ${
                state === 'speaking'
                  ? 'text-emerald-300'
                  : state === 'thinking'
                  ? 'text-amber-300'
                  : state === 'listening'
                  ? 'text-red-300'
                  : 'text-slate-500'
              }`}
            >
              {statusLabel}
            </div>
          </div>
        ) : (
          <div className="h-full w-full overflow-y-auto p-6">
            <AmbientView ambient={ambient} />
          </div>
        )}
      </div>

      {/* ── Bottom half — transcript ──────────────────────────────── */}
      <div className="flex h-1/2 min-h-0 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-2 text-sm">
          {transcript.length === 0 && !partialResponse && (
            <div className="py-8 text-center text-slate-600">
              Press the eye to start talking to {BRAND}.
            </div>
          )}
          {transcript.map((m, i) => (
            <TurnBubble key={i} turn={m} />
          ))}
          {partialResponse && (
            <TurnBubble
              turn={{
                role: 'deek',
                text: partialResponse,
                at: Date.now(),
              }}
              streaming
            />
          )}
          {interim && running && (
            <TurnBubble
              turn={{
                role: 'user',
                text: interim,
                at: Date.now(),
              }}
              streaming
            />
          )}
          <div ref={transcriptEndRef} />
        </div>

        {errorMsg && (
          <div className="mx-4 mb-2 rounded-lg bg-rose-950/60 px-3 py-2 text-xs text-rose-200">
            {errorMsg}
          </div>
        )}

        {/* Big start/stop control */}
        <div className="border-t border-slate-800 p-3">
          <button
            onClick={toggleRunning}
            className={`w-full rounded-xl py-3 text-base font-semibold transition ${
              running
                ? 'bg-rose-600 text-white hover:bg-rose-500'
                : 'bg-emerald-600 text-white hover:bg-emerald-500'
            }`}
          >
            {running ? '■ Stop voice mode' : '🎙 Start voice mode'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────

function TurnBubble({
  turn,
  streaming,
}: {
  turn: VoiceLoopTurn
  streaming?: boolean
}) {
  const isUser = turn.role === 'user'
  const color = isUser
    ? 'bg-slate-800 text-slate-200'
    : turn.outcome === 'backend_error'
    ? 'bg-rose-950/60 text-rose-200'
    : turn.outcome === 'budget_trip'
    ? 'bg-amber-950/60 text-amber-200'
    : 'bg-emerald-950/60 text-emerald-100'
  return (
    <div
      className={`rounded-lg px-3 py-2 ${color} ${streaming ? 'animate-pulse' : ''}`}
    >
      <div className="mb-0.5 text-[10px] uppercase tracking-wider text-slate-500">
        {isUser ? 'You' : BRAND}
      </div>
      {turn.text || (streaming ? '…' : '')}
    </div>
  )
}

function AmbientView({ ambient }: { ambient: AmbientPayload | null }) {
  if (!ambient) {
    return (
      <div className="text-center text-slate-500">Loading ambient data…</div>
    )
  }
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
        <div className="text-xs uppercase tracking-wider text-slate-500">
          {ambient.morning_number.source_module}
        </div>
        <div className="mt-1 text-2xl font-semibold text-slate-100">
          {ambient.morning_number.headline}
        </div>
        {ambient.morning_number.subtitle && (
          <div className="mt-0.5 text-sm text-slate-400">
            {ambient.morning_number.subtitle}
          </div>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {ambient.panels.map(p => (
          <div
            key={p.id}
            className="rounded-xl border border-slate-800 bg-slate-900/60 p-3"
          >
            <div className="mb-2 text-xs uppercase tracking-wider text-slate-500">
              {p.title}
            </div>
            <div className="space-y-1 text-sm">
              {p.items.map((item, i) => (
                <div
                  key={i}
                  className="flex items-start justify-between gap-3"
                >
                  <span className="text-slate-200">{item.label}</span>
                  {item.detail && (
                    <span className="text-right text-xs text-slate-400">
                      {item.detail}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
