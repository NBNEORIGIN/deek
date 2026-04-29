'use client'

/**
 * /voice — top-level shell with:
 * - Chat | Voice mode toggle (state persisted in localStorage)
 * - Shared transcript across modes
 * - Location picker on first visit, cyclable from header
 * - ⋯ menu with Download / Commit / Sign out
 *
 * Auth is handled by middleware (+/voice/login page).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatView } from '@/components/voice/ChatView'
import { VoiceView } from '@/components/voice/VoiceView'
import { BriefingView } from '@/components/voice/BriefingView'
import { TopMenu } from '@/components/voice/TopMenu'
import type { Location, Mode, MeResponse } from '@/components/voice/types'
import type { VoiceLoopTurn } from '@/hooks/useVoiceLoop'
import { BRAND } from '@/lib/brand'

const MODE_KEY = 'deek.mode'
const LOCATION_KEY = 'deek.location'
const SESSION_KEY = 'deek.voice.session'

export default function VoicePage() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [mode, setMode] = useState<Mode>('voice')
  const [location, setLocation] = useState<Location | null>(null)
  const [transcript, setTranscript] = useState<VoiceLoopTurn[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [now, setNow] = useState(new Date())
  const [unseenBriefings, setUnseenBriefings] = useState(0)

  const sessionIdRef = useRef<string | null>(null)

  // ── Clock tick ───────────────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(id)
  }, [])

  // ── Unseen-briefing badge ────────────────────────────────────────
  const refreshBadge = useCallback(async () => {
    try {
      const res = await fetch('/api/voice/briefings/pending', { cache: 'no-store' })
      if (res.ok) {
        const data = await res.json()
        setUnseenBriefings(data.unseen_count || 0)
      }
    } catch {}
  }, [])
  useEffect(() => {
    if (!me) return
    refreshBadge()
    const id = setInterval(refreshBadge, 120_000) // every 2 min
    return () => clearInterval(id)
  }, [me, refreshBadge])
  // Clear badge when user opens the briefing tab
  useEffect(() => {
    if (mode === 'briefing') {
      setUnseenBriefings(0)
      // Server-side seen-marking happens inside BriefingView
      setTimeout(refreshBadge, 2000)
    }
  }, [mode, refreshBadge])

  // ── Boot: fetch session, restore mode + location, hydrate transcript ─
  useEffect(() => {
    const boot = async () => {
      // Session / ACL
      try {
        const res = await fetch('/api/voice/me', { cache: 'no-store' })
        if (res.status === 401) {
          window.location.href =
            '/voice/login?callbackUrl=' + encodeURIComponent('/voice')
          return
        }
        const json: MeResponse = await res.json()
        setMe(json)

        // Restore mode
        const storedMode = localStorage.getItem(MODE_KEY) as Mode | null
        if (storedMode === 'chat' || storedMode === 'voice') {
          setMode(storedMode)
        }

        // Restore location if allowed
        const stored = localStorage.getItem(LOCATION_KEY) as Location | null
        if (stored && (json.allowed_locations || []).includes(stored)) {
          setLocation(stored)
        } else if (json.allowed_locations && json.allowed_locations.length > 0) {
          // First allowed is the default
          setLocation(json.allowed_locations[0])
          localStorage.setItem(LOCATION_KEY, json.allowed_locations[0])
        }

        // Restore session id
        const storedSid = localStorage.getItem(SESSION_KEY)
        if (storedSid) {
          sessionIdRef.current = storedSid
          setSessionId(storedSid)

          // Hydrate transcript (last 20 turns for this session)
          try {
            const sRes = await fetch(
              `/api/voice/sessions?session_id=${encodeURIComponent(storedSid)}&limit=20`,
              { cache: 'no-store' },
            )
            if (sRes.ok) {
              const sData = await sRes.json()
              const turns: VoiceLoopTurn[] = (sData.turns || []).map((t: any) => ({
                role: t.role,
                text: t.text,
                at: new Date(t.at).getTime(),
                outcome: t.outcome,
              }))
              if (turns.length > 0) setTranscript(turns)
            }
          } catch {}
        }
      } catch {
        // Network — allow the user to try; proxies will 401 if session missing
      }
    }
    boot()
  }, [])

  const handleTurn = useCallback((turn: VoiceLoopTurn) => {
    setTranscript(prev => [...prev, turn])
  }, [])

  const handleSessionId = useCallback((id: string) => {
    if (id !== sessionIdRef.current) {
      sessionIdRef.current = id
      setSessionId(id)
      try {
        localStorage.setItem(SESSION_KEY, id)
      } catch {}
    }
  }, [])

  const pickMode = useCallback((m: Mode) => {
    setMode(m)
    try {
      localStorage.setItem(MODE_KEY, m)
    } catch {}
  }, [])

  const pickLocation = useCallback((loc: Location) => {
    setLocation(loc)
    try {
      localStorage.setItem(LOCATION_KEY, loc)
    } catch {}
  }, [])

  // ── Render ─────────────────────────────────────────────────────

  if (!me) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-slate-950 text-slate-500">
        Loading {BRAND}…
      </div>
    )
  }

  const allowed = me.allowed_locations || []
  if (allowed.length === 0) {
    return (
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-3 bg-slate-950 p-6 text-center text-slate-300">
        <div className="text-lg font-semibold">Access restricted</div>
        <div className="max-w-sm text-sm text-slate-500">
          Your role ({me.user?.role || 'unknown'}) does not have access to any
          {BRAND} voice location. Talk to Toby to adjust your permissions.
        </div>
      </div>
    )
  }

  if (!location) {
    return <LocationPicker allowed={allowed} onPick={pickLocation} />
  }

  return (
    <div
      className="flex h-[100dvh] flex-col bg-slate-950 text-slate-100"
      style={{ height: '100dvh' }}
    >
      {/* ── Header ─────────────────────────────────────────── */}
      <header className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-slate-800 bg-slate-900/70 px-3 py-2 backdrop-blur">
        {/* Mode toggle */}
        <div className="flex overflow-hidden rounded-full border border-slate-700 text-xs">
          <button
            onClick={() => pickMode('briefing')}
            className={`relative px-3 py-1 ${mode === 'briefing' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
          >
            Brief
            {unseenBriefings > 0 && mode !== 'briefing' && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-[1rem] items-center justify-center rounded-full bg-emerald-500 px-1 text-[10px] font-semibold text-white">
                {unseenBriefings}
              </span>
            )}
          </button>
          <button
            onClick={() => pickMode('chat')}
            className={`px-3 py-1 ${mode === 'chat' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
          >
            Chat
          </button>
          <button
            onClick={() => pickMode('voice')}
            className={`px-3 py-1 ${mode === 'voice' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'}`}
          >
            Voice
          </button>
        </div>

        {/* Clock */}
        <div className="text-xs text-slate-400 tabular-nums">
          {now.toLocaleTimeString('en-GB', {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>

        {/* Location + menu */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => pickLocation(nextLocation(location, allowed))}
            className="rounded-full border border-slate-700 px-3 py-1 text-xs uppercase tracking-wider text-slate-300 hover:border-slate-500"
            title="Cycle location"
          >
            📍 {location}
          </button>
          <TopMenu
            transcript={transcript}
            sessionId={sessionId}
            userEmail={me.user?.email}
          />
        </div>
      </header>

      {/* ── Body ────────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1 flex-col">
        {mode === 'briefing' ? (
          <BriefingView onTasksChanged={refreshBadge} />
        ) : mode === 'chat' ? (
          <ChatView
            location={location}
            transcript={transcript}
            onTurn={handleTurn}
            sessionId={sessionId}
            onSessionId={handleSessionId}
          />
        ) : (
          <VoiceView
            location={location}
            transcript={transcript}
            onTurn={handleTurn}
            sessionId={sessionId}
            onSessionId={handleSessionId}
          />
        )}
      </div>
    </div>
  )
}

// ── Location picker (first visit) ────────────────────────────────────

function LocationPicker({
  allowed,
  onPick,
}: {
  allowed: Location[]
  onPick: (loc: Location) => void
}) {
  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-6 bg-slate-950 p-6 text-slate-100">
      <div className="text-center">
        <div className="mb-2 text-2xl font-semibold">Where are you?</div>
        <div className="text-sm text-slate-400">
          {BRAND} prioritises information for this location.
        </div>
      </div>
      <div className="flex w-full max-w-sm flex-col gap-3">
        {allowed.includes('workshop') && (
          <LocButton onClick={() => onPick('workshop')} emoji="🔧">
            Workshop
            <span className="text-xs text-slate-400">
              Machines · make list · stock
            </span>
          </LocButton>
        )}
        {allowed.includes('office') && (
          <LocButton onClick={() => onPick('office')} emoji="💼">
            Office
            <span className="text-xs text-slate-400">
              Email · CRM · follow-ups
            </span>
          </LocButton>
        )}
        {allowed.includes('home') && (
          <LocButton onClick={() => onPick('home')} emoji="🏡">
            Home
            <span className="text-xs text-slate-400">
              Cash · revenue · high-level
            </span>
          </LocButton>
        )}
      </div>
    </div>
  )
}

function LocButton({
  onClick,
  emoji,
  children,
}: {
  onClick: () => void
  emoji: string
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-4 rounded-2xl border border-slate-700 bg-slate-900 px-5 py-4 text-left hover:border-emerald-600"
    >
      <span className="text-3xl">{emoji}</span>
      <span className="flex flex-col gap-0.5 text-base font-medium">
        {children}
      </span>
    </button>
  )
}

function nextLocation(l: Location, allowed: Location[]): Location {
  if (allowed.length === 0) return l
  const idx = allowed.indexOf(l)
  if (idx === -1) return allowed[0]
  return allowed[(idx + 1) % allowed.length]
}
