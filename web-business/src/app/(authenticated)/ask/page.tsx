'use client'

import { useEffect, useRef, useState, useCallback, Suspense } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import ChatHistorySidebar from '@/components/chat/ChatHistorySidebar'
import { cleanTitle, stripInjectionBlocks } from '@/components/chat/SessionItem'
import type { SessionMessage } from '@/types/chat'

interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  isError?: boolean
  saved?: boolean
  model_used?: string
  timestamp?: string
}

type VoiceState = 'idle' | 'recording' | 'transcribing' | 'error'

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

    // Table
    if (line.trim().startsWith('|')) {
      const tableLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i])
        i++
      }
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
    return <p className="text-sm text-red-600">{content}</p>
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

// Clipboard / check icon used by the copy buttons on message bubbles.
function CopyIcon({ done }: { done: boolean }) {
  if (done) {
    return (
      <svg
        className="w-3.5 h-3.5 text-emerald-500"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        strokeWidth={2.5}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  return (
    <svg
      className="w-3.5 h-3.5"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
      />
    </svg>
  )
}

// Collapse upstream model IDs into a short tag for the message footer:
// "claude-opus-4-6" → "opus", "claude-sonnet-4-5" → "sonnet",
// "claude-haiku-4-5-20251001" → "haiku", etc. Falls back to the raw string.
function formatModelLabel(rawModel: string): string {
  const m = rawModel.toLowerCase()
  if (m.includes('opus')) return 'opus'
  if (m.includes('sonnet')) return 'sonnet'
  if (m.includes('haiku')) return 'haiku'
  if (m.includes('deepseek')) return 'deepseek'
  if (m.includes('qwen')) return 'qwen'
  if (m.includes('gpt')) return 'gpt'
  return rawModel
}

// ---- Mic button --------------------------------------------------------------

function MicButton({ voiceState, onToggle }: { voiceState: VoiceState; onToggle: () => void }) {
  const isRecording = voiceState === 'recording'
  const isTranscribing = voiceState === 'transcribing'
  const isError = voiceState === 'error'

  let label = 'Start voice input'
  if (isRecording) label = 'Stop recording'
  if (isTranscribing) label = 'Transcribing...'
  if (isError) label = 'Microphone access denied'

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={isTranscribing}
      title={label}
      aria-label={label}
      className={
        'relative flex-shrink-0 flex items-center justify-center w-10 h-10 rounded-lg transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ' +
        (isRecording
          ? 'bg-red-500 text-white animate-pulse'
          : isTranscribing
          ? 'bg-slate-200 text-slate-400 cursor-not-allowed'
          : isError
          ? 'bg-red-50 text-red-400 border border-red-200'
          : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700')
      }
    >
      {isTranscribing ? (
        <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
      ) : (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M12 1a4 4 0 00-4 4v6a4 4 0 008 0V5a4 4 0 00-4-4zm-1 18.93V21h-2v2h6v-2h-2v-1.07A7.003 7.003 0 0019 13h-2a5 5 0 01-10 0H5a7.003 7.003 0 006 6.93z" />
        </svg>
      )}
    </button>
  )
}

// ---- Inner component (uses useSearchParams) ----------------------------------

function AskPageInner() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const prefill = searchParams.get('q') ?? ''
  const urlSessionId = searchParams.get('s')

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(prefill)
  const [sending, setSending] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [speaking, setSpeaking] = useState<string | null>(null)
  const [voiceMode, setVoiceMode] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [sessionId, setSessionId] = useState<string>(
    urlSessionId || generateSessionId()
  )
  const [loadingSession, setLoadingSession] = useState(false)
  const [chatTitle, setChatTitle] = useState<string>('New chat')
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const titleInputRef = useRef<HTMLInputElement>(null)

  const [uploadState, setUploadState] = useState<'idle' | 'uploading'>('idle')
  const [uploadFilename, setUploadFilename] = useState('')
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [copiedMsgId, setCopiedMsgId] = useState<string | null>(null)

  const copyMessage = useCallback(async (msgId: string, text: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        // Fallback for older browsers / non-secure contexts
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
      }
      setCopiedMsgId(msgId)
      setTimeout(() => {
        setCopiedMsgId((current) => (current === msgId ? null : current))
      }, 1500)
    } catch {
      // silent — copy isn't critical
    }
  }, [])

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const pendingAutoSpeakRef = useRef<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const esRef = useRef<EventSource | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const isNewSession = useRef(!urlSessionId)

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    e.target.value = ''
    setPendingFiles((prev) => [...prev, ...files])
    if (!input.trim()) {
      setInput(files.length === 1
        ? 'Analyse this file and summarise the key findings'
        : `Analyse these ${files.length} files and summarise the key findings`)
    }
  }, [input])

  const handleRemoveFile = useCallback((index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index))
  }, [])

  const handleClearFiles = useCallback(() => {
    setPendingFiles([])
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const files = Array.from(e.dataTransfer.files ?? [])
    if (files.length > 0) {
      setPendingFiles((prev) => [...prev, ...files])
      if (!input.trim()) {
        const total = files.length
        setInput(total === 1
          ? 'Analyse this file and summarise the key findings'
          : `Analyse these ${total} files and summarise the key findings`)
      }
    }
  }, [input])

  // Load existing session from URL param
  useEffect(() => {
    if (urlSessionId && urlSessionId !== sessionId) {
      setSessionId(urlSessionId)
      loadSession(urlSessionId)
      lookupSessionTitle(urlSessionId)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlSessionId])

  const loadSession = useCallback(async (sid: string) => {
    setLoadingSession(true)
    try {
      const res = await fetch(`/api/chat/sessions/${sid}`)
      if (res.ok) {
        const data = await res.json()
        const raw: SessionMessage[] = data.messages ?? []
        const msgs: Message[] = raw
          .filter((m) => m.role === 'user' || m.role === 'assistant')
          .map((m, i) => ({
            id: `loaded-${i}`,
            role: m.role as 'user' | 'assistant',
            // Strip any injection blocks that leaked into stored user
            // messages so old conversations don't render with the personality
            // prompt hanging off the top of each user bubble.
            content: m.role === 'user'
              ? stripInjectionBlocks(m.content).trim() || m.content
              : m.content,
            model_used: m.model_used,
            timestamp: m.timestamp,
          }))
        setMessages(msgs)
        isNewSession.current = false

        // Derive title from first user message for the header bar
        const firstUser = msgs.find((m) => m.role === 'user')
        if (firstUser) {
          setChatTitle(cleanTitle(firstUser.content))
        } else {
          setChatTitle('New chat')
        }
      }
    } catch {
      // Session not found — start fresh
    } finally {
      setLoadingSession(false)
    }
  }, [])

  // Lookup the stored title from the sessions list (used when selecting an
  // existing session — the list payload carries an explicit title if set).
  const lookupSessionTitle = useCallback(async (sid: string) => {
    try {
      const res = await fetch('/api/chat/sessions')
      if (!res.ok) return
      const data = await res.json()
      const found = (data.sessions ?? []).find(
        (s: { session_id: string; title?: string }) => s.session_id === sid,
      )
      if (found?.title) setChatTitle(cleanTitle(found.title))
    } catch {
      // silently fail
    }
  }, [])

  function handleSelectSession(sid: string) {
    if (sid === sessionId) return
    // Close any active stream
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    setSending(false)
    setSessionId(sid)
    setMessages([])
    setEditingTitle(false)
    setChatTitle('Loading...')
    isNewSession.current = false
    router.replace(`/ask?s=${sid}`, { scroll: false })
    loadSession(sid)
    lookupSessionTitle(sid)
  }

  function handleNewChat() {
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    setSending(false)
    const newId = generateSessionId()
    setSessionId(newId)
    setMessages([])
    setEditingTitle(false)
    setChatTitle('New chat')
    isNewSession.current = true
    router.replace('/ask', { scroll: false })
  }

  const saveTitle = useCallback(async (nextTitle: string) => {
    const trimmed = nextTitle.trim().slice(0, 80)
    if (!trimmed || trimmed === chatTitle) {
      setEditingTitle(false)
      return
    }
    setChatTitle(trimmed)
    setEditingTitle(false)
    try {
      await fetch(`/api/chat/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: trimmed }),
      })
    } catch {
      // silently fail — title stays updated locally
    }
  }, [chatTitle, sessionId])

  useEffect(() => {
    if (editingTitle && titleInputRef.current) {
      titleInputRef.current.focus()
      titleInputRef.current.select()
    }
  }, [editingTitle])

  const saveToMemory = useCallback(async (msgId: string) => {
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

  const speakMessage = useCallback(async (msgId: string) => {
    const msg = messages.find((m) => m.id === msgId)
    if (!msg || !msg.content) return

    if (speaking === msgId && audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
      setSpeaking(null)
      return
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }

    setSpeaking(msgId)
    try {
      const plainText = msg.content
        .replace(/\*\*(.*?)\*\*/g, '$1')
        .replace(/\*(.*?)\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/#{1,3}\s/g, '')
        .replace(/\|[^\n]+\|/g, '')
        .replace(/---/g, '')
        .replace(/\n+/g, '. ')
        .trim()

      const res = await fetch('/api/voice/speak', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: plainText }),
      })
      if (!res.ok) { setSpeaking(null); return }
      const audioBlob = await res.blob()
      const url = URL.createObjectURL(audioBlob)
      const audio = new Audio(url)
      audioRef.current = audio
      audio.onended = () => { setSpeaking(null); audioRef.current = null; URL.revokeObjectURL(url) }
      audio.play()
    } catch {
      setSpeaking(null)
    }
  }, [messages, speaking])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim()
    if (!text || sending) return

    // Show user message (include file indicators if attached)
    const fileLabel = pendingFiles.length > 0
      ? pendingFiles.map((f) => `📎 ${f.name}`).join('\n') + '\n'
      : ''
    const nowIso = new Date().toISOString()
    const userMsg: Message = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: fileLabel + text,
      timestamp: nowIso,
    }
    setMessages((prev) => {
      // Derive the header title from the first user message in a new session
      if (prev.length === 0 && (chatTitle === 'New chat' || chatTitle === 'Loading...')) {
        setChatTitle(cleanTitle(text))
      }
      return [...prev, userMsg]
    })
    setInput('')
    setSending(true)

    if (esRef.current) { esRef.current.close(); esRef.current = null }

    // If there are pending files, upload them sequentially and prepend results
    let fileContext = ''
    if (pendingFiles.length > 0) {
      setUploadState('uploading')
      const results: string[] = []
      for (const file of pendingFiles) {
        setUploadFilename(file.name)
        try {
          const formData = new FormData()
          formData.append('file', file)
          const uploadRes = await fetch('/api/chat/upload', { method: 'POST', body: formData })
          const uploadData = await uploadRes.json()
          if (uploadData.success) {
            results.push(`[FILE UPLOADED: ${file.name} — ${uploadData.summary}]`)
          } else {
            results.push(`[FILE UPLOAD FAILED: ${file.name} — ${uploadData.summary}]`)
          }
        } catch {
          results.push(`[FILE UPLOAD FAILED: ${file.name} — could not reach server]`)
        }
      }
      fileContext = results.join('\n') + '\n\n'
      setUploadState('idle')
      setUploadFilename('')
      setPendingFiles([])
    }

    const assistantId = `a-${Date.now()}`
    setMessages((prev) => [
      ...prev,
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
      },
    ])

    if (voiceMode) pendingAutoSpeakRef.current = assistantId

    // Update URL on first message of a new session
    if (isNewSession.current) {
      isNewSession.current = false
      router.replace(`/ask?s=${sessionId}`, { scroll: false })
    }

    // Use POST + streaming fetch instead of EventSource to avoid URL length limits
    const abortController = new AbortController()
    esRef.current = { close: () => abortController.abort() } as EventSource

    try {
      const streamRes = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project: 'nbne',
          session_id: sessionId,
          message: fileContext + text,
          channel: 'web',
        }),
        signal: abortController.signal,
      })

      if (!streamRes.ok || !streamRes.body) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, content: 'Failed to reach the server.', isError: true } : m
          )
        )
        setSending(false)
        return
      }

      const reader = streamRes.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6).trim()
          if (!jsonStr) continue

          try {
            const parsed = JSON.parse(jsonStr)
            const type: string = parsed.type ?? ''

            if (type === 'response_delta') {
              const chunk: string = parsed.text ?? ''
              if (chunk) {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId ? { ...m, content: m.content + chunk } : m
                  )
                )
              }
            } else if (type === 'complete') {
              const completedModel: string = parsed.model_used ?? ''
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== assistantId) return m
                  const nextContent = m.content || (parsed.response ?? '')
                  return {
                    ...m,
                    content: nextContent,
                    model_used: completedModel || m.model_used,
                  }
                })
              )
              if (pendingAutoSpeakRef.current === assistantId) {
                pendingAutoSpeakRef.current = null
                setTimeout(() => speakMessage(assistantId), 100)
              }
            } else if (type === 'error') {
              const errMsg: string = parsed.message ?? parsed.error ?? 'An error occurred'
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, content: errMsg, isError: true } : m
                )
              )
            }
          } catch {
            // Non-JSON line — ignore
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId && !m.content ? { ...m, content: 'Connection lost.', isError: true } : m
          )
        )
      }
    } finally {
      esRef.current = null
      setSending(false)
    }
  }, [input, sending, sessionId, router, speakMessage, voiceMode, pendingFiles, chatTitle])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  // ---- Voice recording -------------------------------------------------------

  const startRecording = useCallback(async () => {
    setVoiceState('recording')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setVoiceState('error')
      setTimeout(() => setVoiceState('idle'), 2500)
      return
    }

    chunksRef.current = []
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm'

    const recorder = new MediaRecorder(stream, { mimeType })
    mediaRecorderRef.current = recorder

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      setVoiceState('transcribing')

      const blob = new Blob(chunksRef.current, { type: mimeType })
      const formData = new FormData()
      formData.append('audio', blob, 'recording.webm')

      try {
        const res = await fetch('/api/voice/transcribe', { method: 'POST', body: formData })
        if (res.ok) {
          const data = await res.json()
          const transcribed: string = data.text ?? ''
          if (transcribed.trim()) {
            setInput(transcribed)
            setVoiceState('idle')
            setVoiceMode(true)
            setTimeout(() => sendMessage(transcribed), 0)
          } else {
            setVoiceState('idle')
          }
        } else {
          setVoiceState('idle')
        }
      } catch {
        setVoiceState('idle')
      }
    }

    recorder.start()
  }, [sendMessage])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && voiceState === 'recording') {
      mediaRecorderRef.current.stop()
    }
  }, [voiceState])

  const handleVoiceToggle = useCallback(() => {
    if (voiceState === 'idle') startRecording()
    else if (voiceState === 'recording') stopRecording()
  }, [voiceState, startRecording, stopRecording])

  return (
    <div
      // Cancel the authenticated layout's p-4 md:p-6 padding and lock the
      // height to exactly the visible area below the fixed header. This
      // prevents the outer <main> from ever scrolling the ask page so the
      // chat title cannot disappear off the top of the window.
      className="flex -m-4 md:-m-6 h-[calc(100dvh-56px)] overflow-hidden"
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Toolbar: current chat title (click-to-edit) + mobile history toggle.
            Sticky + z-20 so the title stays pinned even if any ancestor ends
            up scrolling for unexpected reasons. */}
        <div className="sticky top-0 z-20 flex items-center gap-3 px-4 py-2 border-b border-slate-100 bg-white flex-shrink-0">
          <div className="flex-1 min-w-0">
            {editingTitle ? (
              <input
                ref={titleInputRef}
                type="text"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => saveTitle(titleDraft)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    saveTitle(titleDraft)
                  } else if (e.key === 'Escape') {
                    setEditingTitle(false)
                  }
                }}
                maxLength={80}
                className="w-full text-base font-semibold text-slate-800 bg-white border border-indigo-300 rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            ) : (
              <button
                type="button"
                onClick={() => {
                  setTitleDraft(chatTitle === 'New chat' ? '' : chatTitle)
                  setEditingTitle(true)
                }}
                title="Click to rename this chat"
                className="group flex items-center gap-1.5 text-left max-w-full min-w-0 text-base font-semibold text-slate-800 hover:text-indigo-700 transition-colors truncate"
              >
                <span className="truncate">{chatTitle}</span>
                <svg
                  className="w-3.5 h-3.5 text-slate-300 group-hover:text-indigo-400 flex-shrink-0"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536M9 11l6.293-6.293a1 1 0 011.414 0l2.586 2.586a1 1 0 010 1.414L13 15H9v-4z" />
                </svg>
              </button>
            )}
          </div>

          <button
            onClick={() => setHistoryOpen(!historyOpen)}
            className="lg:hidden flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-slate-600 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-lg transition-colors flex-shrink-0"
            title="Chat history"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            History
          </button>
        </div>

        {/* Loading state for session */}
        {loadingSession ? (
          <div className="flex-1 flex items-center justify-center min-h-0">
            <p className="text-sm text-slate-400 animate-pulse">Loading conversation...</p>
          </div>
        ) : (
          <>
            {/* Message list — flex-1 + min-h-0 is what lets the internal
                overflow-y-auto actually scroll inside a column flex. */}
            <div className="flex-1 min-h-0 overflow-y-auto space-y-4 py-4 px-4 max-w-[960px] mx-auto w-full">
              {messages.length === 0 && (
                <div className="flex items-center justify-center h-full px-4">
                  <p className="text-sm text-slate-400 text-center">
                    Ask anything about the business — stock, orders, processes, financials.
                  </p>
                </div>
              )}

              {messages.map((msg) => {
                const hoverTime = msg.timestamp
                  ? new Date(msg.timestamp).toLocaleString('en-GB', {
                      day: 'numeric',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : undefined
                const isCopied = copiedMsgId === msg.id
                return msg.role === 'user' ? (
                  <div key={msg.id} className="group flex justify-end items-start gap-1.5">
                    <button
                      type="button"
                      onClick={() => copyMessage(msg.id, msg.content)}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 mt-1.5 p-1 rounded text-slate-300 hover:text-indigo-600 transition-opacity"
                      title={isCopied ? 'Copied' : 'Copy message'}
                      aria-label="Copy user message"
                    >
                      <CopyIcon done={isCopied} />
                    </button>
                    <div
                      className="max-w-[85%] md:max-w-[75%] bg-indigo-600 text-white text-sm px-3 py-2.5 md:px-4 md:py-3 rounded-2xl rounded-tr-sm whitespace-pre-wrap"
                      title={hoverTime}
                    >
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div key={msg.id} className="group flex justify-start items-start gap-1.5">
                    <div
                      className="max-w-[85%] md:max-w-[75%] bg-white border border-slate-200 text-slate-800 text-sm px-3 py-2.5 md:px-4 md:py-3 rounded-2xl rounded-tl-sm shadow-sm"
                      title={hoverTime}
                    >
                      {msg.content ? (
                        <>
                          <AssistantBubble content={msg.content} isError={msg.isError} />
                          {!msg.isError && !sending && (
                            <div className="mt-2 pt-2 border-t border-slate-100 flex items-center gap-3 flex-wrap">
                              <button
                                onClick={() => copyMessage(msg.id, msg.content)}
                                className="text-xs text-slate-400 hover:text-indigo-600 transition-colors flex items-center gap-1"
                                title={isCopied ? 'Copied' : 'Copy reply'}
                              >
                                <CopyIcon done={isCopied} />
                                {isCopied ? 'Copied' : 'Copy'}
                              </button>
                              <button
                                onClick={() => speakMessage(msg.id)}
                                className="text-xs text-slate-400 hover:text-indigo-600 transition-colors"
                                title={speaking === msg.id ? 'Stop speaking' : 'Read aloud'}
                              >
                                {speaking === msg.id ? 'Stop' : 'Listen'}
                              </button>
                              {msg.saved ? (
                                <span className="text-xs text-emerald-600">Saved to memory</span>
                              ) : (
                                <button
                                  onClick={() => saveToMemory(msg.id)}
                                  disabled={saving === msg.id}
                                  className="text-xs text-slate-400 hover:text-indigo-600 transition-colors disabled:opacity-50"
                                >
                                  {saving === msg.id ? 'Saving...' : 'Remember this'}
                                </button>
                              )}
                              {msg.model_used && (
                                <span
                                  className="text-[10px] text-slate-300 ml-auto"
                                  title={`Answered by ${msg.model_used}`}
                                >
                                  {formatModelLabel(msg.model_used)}
                                </span>
                              )}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className="text-slate-400 animate-pulse">Thinking...</span>
                      )}
                    </div>
                  </div>
                )
              })}
              <div ref={bottomRef} />
            </div>

            {/* Recording/voice indicators */}
            {voiceState === 'recording' && (
              <div className="flex items-center gap-2 px-3 py-2 mb-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 font-medium">
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                Recording — tap mic to stop
              </div>
            )}
            {voiceState === 'transcribing' && (
              <div className="flex items-center gap-2 px-3 py-2 mb-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-500">
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Transcribing...
              </div>
            )}
            {voiceState === 'error' && (
              <div className="px-3 py-2 mb-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
                Microphone access denied
              </div>
            )}
            {voiceMode && voiceState === 'idle' && !sending && (
              <div className="flex items-center justify-between px-3 py-1.5 mb-2 bg-indigo-50 border border-indigo-200 rounded-lg">
                <span className="text-xs text-indigo-600 font-medium">Voice mode — responses will be read aloud</span>
                <button
                  onClick={() => setVoiceMode(false)}
                  className="text-xs text-indigo-400 hover:text-indigo-700 ml-2"
                >
                  Turn off
                </button>
              </div>
            )}

            {/* Pending files indicator */}
            {pendingFiles.length > 0 && uploadState !== 'uploading' && (
              <div className="px-3 py-2 mb-2 bg-slate-50 border border-slate-200 rounded-lg max-w-[960px] mx-auto w-full space-y-1">
                {pendingFiles.map((file, i) => (
                  <div key={`${file.name}-${i}`} className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-xs text-slate-600 font-medium">
                      <span>📎</span>
                      <span className="truncate max-w-[200px] md:max-w-[400px]">{file.name}</span>
                      <span className="text-slate-400">({(file.size / 1024).toFixed(0)} KB)</span>
                    </div>
                    <button onClick={() => handleRemoveFile(i)} className="text-xs text-slate-400 hover:text-red-500 ml-2">✕</button>
                  </div>
                ))}
                {pendingFiles.length > 1 && (
                  <div className="flex justify-between items-center pt-1 border-t border-slate-100">
                    <span className="text-xs text-slate-400">{pendingFiles.length} files attached</span>
                    <button onClick={handleClearFiles} className="text-xs text-slate-400 hover:text-red-500">Remove all</button>
                  </div>
                )}
              </div>
            )}

            {/* File uploading indicator */}
            {uploadState === 'uploading' && (
              <div className="flex items-center gap-2 px-3 py-2 mb-2 bg-indigo-50 border border-indigo-200 rounded-lg text-xs text-indigo-600 font-medium max-w-[960px] mx-auto w-full">
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Processing {uploadFilename}...
              </div>
            )}

            {/* Input bar */}
            <div className="bg-white border border-slate-200 rounded-xl p-2.5 md:p-3 flex gap-2 md:gap-3 items-end shadow-sm flex-shrink-0 max-w-[960px] mx-auto w-full">
              {/* File attach button */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                accept=".csv,.xlsm,.xlsx,.tsv,.txt,.md,.pdf,.docx"
                onChange={handleFileSelect}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || uploadState === 'uploading'}
                title="Attach file (reports, documents)"
                className="flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center transition-colors bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700 disabled:opacity-50"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                </svg>
              </button>
              <div className="flex-1 flex flex-col min-w-0">
                <textarea
                  className="w-full resize-none text-sm text-slate-800 placeholder-slate-400 focus:outline-none min-h-[40px] max-h-[160px] overflow-y-auto px-1 md:px-0 bg-transparent"
                  rows={1}
                  placeholder="Ask anything..."
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={sending}
                />
                <span className="hidden md:block text-[10px] text-slate-300 mt-0.5 select-none">
                  Press Enter to send, Shift+Enter for a new line
                </span>
              </div>
              <MicButton voiceState={voiceState} onToggle={handleVoiceToggle} />
              <button
                onClick={() => sendMessage()}
                disabled={sending || !input.trim()}
                className="flex-shrink-0 px-3 py-2 md:px-4 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed h-10"
              >
                Send
              </button>
            </div>
          </>
        )}
      </div>

      {/* Desktop: always-visible right panel */}
      <div className="hidden lg:block">
        <ChatHistorySidebar
          isOpen={true}
          onClose={() => {}}
          currentSessionId={sessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
        />
      </div>

      {/* Mobile: toggleable overlay */}
      <div className="lg:hidden">
        <ChatHistorySidebar
          isOpen={historyOpen}
          onClose={() => setHistoryOpen(false)}
          currentSessionId={sessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
        />
      </div>
    </div>
  )
}

// ---- Page export -------------------------------------------------------------

export default function AskPage() {
  return (
    <Suspense fallback={<div className="text-sm text-slate-400">Loading...</div>}>
      <AskPageInner />
    </Suspense>
  )
}
