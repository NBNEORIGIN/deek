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
import {
  Send, LogOut, FileText, Plus, Menu, X, Loader2, Check,
  Paperclip, FileCheck, AlertCircle,
} from 'lucide-react'
import { BRAND } from '@/lib/brand'

interface Turn {
  role: 'user' | 'assistant'
  text: string
  at: number
}

interface ToolEvent {
  tool: string
  startedAt: number
  durationMs?: number
}

interface StagedFile {
  id: string                  // local-only identifier (uuid)
  file: File
  status: 'pending' | 'uploading' | 'ready' | 'error'
  text?: string               // extracted text once uploaded
  error?: string
  truncated?: boolean
  supported?: boolean
}

const MAX_FILES = 5
const MAX_FILE_BYTES = 10 * 1024 * 1024
const ACCEPT = '.pdf,.docx,.csv,.tsv,.xlsx,.xlsm,.txt,.md,.log,.json,.png,.jpg,.jpeg,.heic,.gif,.webp,.bmp'

function _newFileId(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function _humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function _buildAttachmentText(files: StagedFile[]): string {
  const ready = files.filter(f => f.status === 'ready' && f.text)
  if (ready.length === 0) return ''
  const blocks = ready.map(f => {
    const trunc = f.truncated ? ' (truncated)' : ''
    return `[Attached: ${f.file.name}${trunc}]\n${f.text}\n[/Attached]`
  })
  return blocks.join('\n\n')
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
  const [tools, setTools] = useState<ToolEvent[]>([])
  const [stagedFiles, setStagedFiles] = useState<StagedFile[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

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
    setStagedFiles([])
    textareaRef.current?.focus()
  }, [])

  // ── File staging ───────────────────────────────────────────────────
  const stageFiles = useCallback((newFiles: File[]) => {
    setStagedFiles(current => {
      const remaining = MAX_FILES - current.length
      if (remaining <= 0) return current
      const accepted: StagedFile[] = []
      for (const f of newFiles.slice(0, remaining)) {
        if (f.size > MAX_FILE_BYTES) {
          accepted.push({
            id: _newFileId(),
            file: f,
            status: 'error',
            error: `${f.name} exceeds 10MB limit`,
          })
        } else {
          accepted.push({ id: _newFileId(), file: f, status: 'pending' })
        }
      }
      return [...current, ...accepted]
    })
  }, [])

  const removeFile = useCallback((id: string) => {
    setStagedFiles(current => current.filter(f => f.id !== id))
  }, [])

  const clearStagedFiles = useCallback(() => setStagedFiles([]), [])

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = Array.from(e.target.files || [])
      if (list.length > 0) stageFiles(list)
      // Reset so the same file can be re-selected if removed
      if (e.target) e.target.value = ''
    },
    [stageFiles],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    // Only clear when leaving the outer drop zone
    if (e.currentTarget === e.target) setDragActive(false)
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setDragActive(false)
      const list = Array.from(e.dataTransfer.files || [])
      if (list.length > 0) stageFiles(list)
    },
    [stageFiles],
  )

  const uploadStaged = useCallback(async (): Promise<string> => {
    const toUpload = stagedFiles.filter(f => f.status === 'pending')
    if (toUpload.length === 0) {
      // Build context text from already-ready files (re-send case)
      return _buildAttachmentText(stagedFiles)
    }

    setStagedFiles(cur =>
      cur.map(f => (f.status === 'pending' ? { ...f, status: 'uploading' } : f)),
    )

    const fd = new FormData()
    for (const sf of toUpload) {
      fd.append('files', sf.file, sf.file.name)
    }

    try {
      const res = await fetch('/api/voice/upload', { method: 'POST', body: fd })
      if (res.status === 401) {
        window.location.href = '/voice/login?callbackUrl=/voice'
        return ''
      }
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = data?.detail || data?.error || `upload HTTP ${res.status}`
        setStagedFiles(cur =>
          cur.map(f =>
            toUpload.some(u => u.id === f.id)
              ? { ...f, status: 'error', error: msg }
              : f,
          ),
        )
        return ''
      }
      const out: any[] = data.files || []
      // Match returned items to staged files in order (one-to-one)
      setStagedFiles(cur => {
        const next = [...cur]
        for (let i = 0; i < toUpload.length; i++) {
          const sf = toUpload[i]
          const r = out[i]
          const idx = next.findIndex(x => x.id === sf.id)
          if (idx === -1) continue
          if (r?.error || !r?.supported) {
            next[idx] = {
              ...next[idx],
              status: 'error',
              error: r?.error || 'unsupported',
              supported: !!r?.supported,
            }
          } else {
            next[idx] = {
              ...next[idx],
              status: 'ready',
              text: r.text || '',
              truncated: !!r.truncated,
              supported: true,
            }
          }
        }
        return next
      })

      // Re-read state synchronously by combining the returned data
      const combined: StagedFile[] = stagedFiles.map(sf => {
        const i = toUpload.findIndex(u => u.id === sf.id)
        if (i === -1) return sf
        const r = out[i]
        if (r?.error || !r?.supported) {
          return { ...sf, status: 'error', error: r?.error || 'unsupported', supported: !!r?.supported }
        }
        return { ...sf, status: 'ready', text: r.text || '', truncated: !!r.truncated, supported: true }
      })
      return _buildAttachmentText(combined)
    } catch (err: any) {
      setStagedFiles(cur =>
        cur.map(f =>
          toUpload.some(u => u.id === f.id)
            ? { ...f, status: 'error', error: err?.message || 'upload error' }
            : f,
        ),
      )
      return ''
    }
  }, [stagedFiles])

  // ── Submit ─────────────────────────────────────────────────────────
  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      const hasFiles = stagedFiles.length > 0
      if (!trimmed && !hasFiles) return
      if (busy) return

      setBusy(true)
      setPartial('')
      setErrorMsg(null)
      setTools([])

      // 1) If there are staged files, upload + extract first.
      let attachmentText = ''
      if (hasFiles) {
        attachmentText = await uploadStaged()
        // If every file errored, abort the send.
        const allErrored = stagedFiles.every(f => f.status === 'error')
        if (!attachmentText && allErrored) {
          setBusy(false)
          setErrorMsg('Couldn\'t process any of the attached files.')
          return
        }
      }

      // 2) Build the user-visible message + the agent payload.
      const userVisible = trimmed || (hasFiles
        ? 'Analyse the attached file(s) and summarise the key findings.'
        : '')
      const payload = attachmentText
        ? `${attachmentText}\n\n${userVisible}`
        : userVisible

      setTranscript(t => [
        ...t,
        { role: 'user', text: userVisible, at: Date.now() },
      ])
      setInput('')
      // Files are now consumed — clear the chips so they don't re-attach.
      setStagedFiles([])

      abortRef.current?.abort()
      abortRef.current = new AbortController()

      try {
        const res = await fetch('/api/voice/chat/agent-stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: payload,
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
            } else if (eventType === 'tool_start') {
              setTools(ts => [
                ...ts,
                { tool: String(data.tool || 'tool'), startedAt: Date.now() },
              ])
            } else if (eventType === 'tool_end') {
              const t = String(data.tool || 'tool')
              const dur = Number(data.duration_ms || 0)
              setTools(ts =>
                ts.map(x =>
                  x.tool === t && x.durationMs === undefined
                    ? { ...x, durationMs: dur }
                    : x,
                ),
              )
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
              setTools([])
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
    [busy, loadHistory, sessionId, stagedFiles, uploadStaged],
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
      <div
        className="relative flex h-full min-w-0 flex-1 flex-col"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {dragActive && (
          <div className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center bg-emerald-50/80 backdrop-blur-sm">
            <div className="rounded-2xl border-2 border-dashed border-emerald-500 bg-white px-8 py-6 text-center text-emerald-700">
              <Paperclip size={28} className="mx-auto mb-2" />
              <div className="text-sm font-medium">Drop files to attach</div>
              <div className="mt-1 text-xs text-emerald-600">
                PDF · DOCX · CSV · XLSX · TXT · MD · JSON
              </div>
            </div>
          </div>
        )}

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

            {/* Thinking pane — shows tool activity while busy and no
                response text has started streaming yet. Once Rex starts
                writing the answer, the pane stays visible above the
                streaming bubble so you can see what it consulted. */}
            {busy && tools.length > 0 && (
              <ThinkingPane tools={tools} />
            )}

            {partial && (
              <Bubble
                turn={{ role: 'assistant', text: partial, at: Date.now() }}
                streaming
              />
            )}

            {/* Thinking-only state — busy, no tools yet, no partial yet.
                Shows a subtle "Thinking…" so the user sees progress. */}
            {busy && tools.length === 0 && !partial && (
              <div className="flex items-center gap-2 px-4 py-2 text-sm text-gray-500">
                <Loader2 size={14} className="animate-spin" />
                <span>{BRAND} is thinking…</span>
              </div>
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
          className="flex flex-shrink-0 flex-col gap-2 border-t border-gray-200 px-3 py-3"
        >
          {/* Staged file chips */}
          {stagedFiles.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {stagedFiles.map(sf => (
                <FileChip key={sf.id} file={sf} onRemove={() => removeFile(sf.id)} />
              ))}
              {stagedFiles.length > 1 && (
                <button
                  type="button"
                  onClick={clearStagedFiles}
                  className="text-xs text-gray-500 hover:text-gray-900"
                >
                  Remove all
                </button>
              )}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={handleFileInput}
          />

          <div className="flex items-end gap-2">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || stagedFiles.length >= MAX_FILES}
              className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full border border-gray-300 text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-30"
              title={
                stagedFiles.length >= MAX_FILES
                  ? `Max ${MAX_FILES} files per message`
                  : 'Attach file'
              }
            >
              <Paperclip size={18} />
            </button>

            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              rows={1}
              autoFocus
              disabled={busy}
              placeholder={
                stagedFiles.length > 0
                  ? `Ask about ${stagedFiles.length === 1 ? 'this file' : 'these files'}…`
                  : `Message ${BRAND}…`
              }
              className="flex-1 resize-none rounded-2xl border border-gray-300 bg-white px-4 py-3 text-base text-gray-900 placeholder-gray-400 focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400 disabled:opacity-50"
              style={{ maxHeight: '10rem' }}
            />

            <button
              type="submit"
              disabled={busy || (!input.trim() && stagedFiles.length === 0)}
              className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full bg-gray-900 text-white transition hover:bg-gray-800 disabled:opacity-30"
              title="Send"
            >
              <Send size={18} />
            </button>
          </div>
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

// ── File chip ─────────────────────────────────────────────────────────

function FileChip({
  file,
  onRemove,
}: {
  file: StagedFile
  onRemove: () => void
}) {
  const isError = file.status === 'error'
  const isReady = file.status === 'ready'
  const isUploading = file.status === 'uploading'
  return (
    <div
      className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${
        isError
          ? 'border-rose-300 bg-rose-50 text-rose-700'
          : isReady
            ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
            : 'border-gray-300 bg-gray-50 text-gray-700'
      }`}
      title={file.error || file.file.name}
    >
      {isUploading ? (
        <Loader2 size={12} className="animate-spin" />
      ) : isReady ? (
        <FileCheck size={12} />
      ) : isError ? (
        <AlertCircle size={12} />
      ) : (
        <FileText size={12} />
      )}
      <span className="max-w-[14rem] truncate font-medium">{file.file.name}</span>
      <span className="text-gray-500">{_humanSize(file.file.size)}</span>
      {file.truncated && (
        <span className="text-amber-600">truncated</span>
      )}
      {isError && (
        <span className="text-rose-600">{file.error || 'error'}</span>
      )}
      <button
        type="button"
        onClick={onRemove}
        className="ml-1 rounded-full p-0.5 text-gray-500 hover:bg-gray-200 hover:text-gray-900"
        title="Remove"
      >
        <X size={12} />
      </button>
    </div>
  )
}

// ── Thinking pane ─────────────────────────────────────────────────────

const TOOL_LABEL: Record<string, string> = {
  search_emails: 'Searching your email…',
  search_crm: 'Looking in the CRM…',
  search_wiki: 'Searching memory + wiki…',
  query_amazon_intel: 'Querying Amazon intel…',
  retrieve_codebase_context: 'Pulling code context…',
  retrieve_chat_history: 'Recalling earlier chats…',
  search_similar_quotes: 'Looking for similar quotes…',
  get_quote_context: 'Loading quote context…',
  get_module_snapshot: 'Reading module snapshot…',
  retrieve_similar_decisions: 'Recalling similar decisions…',
  analyze_enquiry: 'Analysing the enquiry…',
}

function ThinkingPane({ tools }: { tools: ToolEvent[] }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm">
      <ul className="space-y-1.5">
        {tools.map((t, i) => {
          const done = t.durationMs !== undefined
          const label = TOOL_LABEL[t.tool] || t.tool
          const secs =
            done && (t.durationMs || 0) > 0
              ? Math.max(0.1, Math.round((t.durationMs || 0) / 100) / 10)
              : null
          return (
            <li key={`${t.tool}-${i}`} className="flex items-center gap-2">
              {done ? (
                <Check size={13} className="text-emerald-600" />
              ) : (
                <Loader2 size={13} className="animate-spin text-gray-500" />
              )}
              <span className={done ? 'text-gray-700' : 'text-gray-900'}>
                {label}
              </span>
              {secs !== null && (
                <span className="ml-auto text-xs text-gray-400">
                  {secs}s
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
