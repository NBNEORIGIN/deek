'use client'

/**
 * The ⋯ dropdown menu in the header.
 * Download transcript · Commit to memory · Logout.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { VoiceLoopTurn } from '@/hooks/useVoiceLoop'
import { listHalCandidates, halTuningFor } from '@/lib/speechQueue'
import { BRAND } from '@/lib/brand'

const VOICE_PREF_KEY = 'deek.voice.preferred'

export function TopMenu({
  transcript,
  sessionId,
  userEmail,
}: {
  transcript: VoiceLoopTurn[]
  sessionId: string | null
  userEmail?: string
}) {
  const [open, setOpen] = useState(false)
  const [working, setWorking] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [showVoices, setShowVoices] = useState(false)
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([])
  const [currentVoice, setCurrentVoice] = useState<string>('')
  const ref = useRef<HTMLDivElement>(null)

  // Load voices (browser loads them async)
  useEffect(() => {
    const load = () => {
      const list = listHalCandidates()
      setVoices(list)
    }
    load()
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.addEventListener('voiceschanged', load)
      return () => {
        window.speechSynthesis.removeEventListener('voiceschanged', load)
      }
    }
  }, [])

  useEffect(() => {
    setCurrentVoice(localStorage.getItem(VOICE_PREF_KEY) || '')
  }, [])

  const pickVoice = useCallback((name: string) => {
    setCurrentVoice(name)
    try {
      if (name) localStorage.setItem(VOICE_PREF_KEY, name)
      else localStorage.removeItem(VOICE_PREF_KEY)
    } catch {}
    // Speak a sample with the new voice
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel()
      const u = new SpeechSynthesisUtterance(`I am ${BRAND}, your sovereign business brain.`)
      const v = voices.find(x => x.name === name)
      if (v) u.voice = v
      const tuned = halTuningFor(v?.name)
      u.rate = tuned.rate
      u.pitch = tuned.pitch
      u.lang = v?.lang || 'en-GB'
      window.speechSynthesis.speak(u)
    }
  }, [voices])

  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener('mousedown', onClick)
    return () => window.removeEventListener('mousedown', onClick)
  }, [open])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 5000)
  }

  const handleDownload = useCallback(() => {
    if (transcript.length === 0) {
      showToast('Nothing to download yet.')
      return
    }
    const now = new Date()
    const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}-${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}`
    const lines: string[] = []
    lines.push(`# ${BRAND} transcript — ${stamp}`)
    if (sessionId) lines.push(`\nSession: \`${sessionId}\``)
    if (userEmail) lines.push(`User: ${userEmail}`)
    lines.push('')
    for (const t of transcript) {
      const who = t.role === 'user' ? 'User' : BRAND
      lines.push(`**${who}:** ${t.text}`)
      lines.push('')
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `deek-transcript-${stamp}.md`
    a.click()
    URL.revokeObjectURL(url)
    setOpen(false)
  }, [transcript, sessionId, userEmail])

  const handleCommit = useCallback(async () => {
    if (!sessionId) {
      showToast('Start a conversation first — no session to commit.')
      return
    }
    setOpen(false)
    setWorking('Committing to memory…')
    try {
      const res = await fetch('/api/voice/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      })
      const data = await res.json()
      if (!res.ok) {
        showToast(
          `Commit failed: ${data?.detail || data?.error || res.status}`,
        )
      } else {
        showToast(
          `Saved as "${data.title}" · ${data.turn_count} turns · searchable`,
        )
      }
    } catch (err: any) {
      showToast(`Commit failed: ${err?.message || err}`)
    } finally {
      setWorking(null)
    }
  }, [sessionId])

  const handleLogout = useCallback(async () => {
    await fetch('/api/voice/logout', { method: 'POST' })
    window.location.href = '/voice/login'
  }, [])

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        aria-label="Menu"
        className="rounded-lg px-2 py-1 text-lg text-slate-300 hover:bg-slate-800"
      >
        ⋯
      </button>
      {open && !showVoices && (
        <div className="absolute right-0 top-full mt-2 w-60 overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-lg">
          <MenuItem onClick={handleDownload} label="Download transcript" hint=".md file" />
          <MenuItem onClick={handleCommit} label="Commit to memory" hint="save as wiki article" />
          <MenuItem
            onClick={() => setShowVoices(true)}
            label="Voice…"
            hint={currentVoice || 'auto — best available'}
          />
          <div className="border-t border-slate-800" />
          <MenuItem onClick={handleLogout} label="Sign out" />
        </div>
      )}

      {open && showVoices && (
        <div className="absolute right-0 top-full mt-2 max-h-[70vh] w-72 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2 text-xs uppercase tracking-wider text-slate-400">
            <button
              onClick={() => setShowVoices(false)}
              className="hover:text-slate-200"
            >
              ← Back
            </button>
            <span>{BRAND} voice</span>
          </div>
          <button
            onClick={() => pickVoice('')}
            className={`flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-slate-800 ${
              !currentVoice ? 'bg-slate-800 text-emerald-300' : 'text-slate-200'
            }`}
          >
            <span>Auto (best available)</span>
            <span className="text-xs text-slate-500">Picks a HAL-like voice on this device</span>
          </button>
          {voices.length === 0 && (
            <div className="px-3 py-4 text-xs text-slate-500">
              No voices available — TTS may not be supported on this browser.
            </div>
          )}
          {voices.map(v => (
            <button
              key={v.name}
              onClick={() => pickVoice(v.name)}
              className={`flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-slate-800 ${
                currentVoice === v.name ? 'bg-slate-800 text-emerald-300' : 'text-slate-200'
              }`}
            >
              <span>{v.name}</span>
              <span className="text-xs text-slate-500">{v.lang}</span>
            </button>
          ))}
        </div>
      )}
      {(working || toast) && (
        <div className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-slate-800 px-4 py-2 text-xs text-slate-100 shadow-xl">
          {working || toast}
        </div>
      )}
    </div>
  )
}

function MenuItem({
  onClick,
  label,
  hint,
}: {
  onClick: () => void
  label: string
  hint?: string
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full flex-col items-start px-3 py-2 text-left text-sm text-slate-200 hover:bg-slate-800"
    >
      <span>{label}</span>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </button>
  )
}
