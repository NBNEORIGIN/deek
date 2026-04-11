'use client'

import { useState, useRef, useEffect, useCallback, KeyboardEvent, ReactNode } from 'react'
import {
  FileText,
  Folder,
  Code2,
  MessageSquare,
  Pin,
  Globe,
  BookOpen,
  Search,
  PencilLine,
  FlaskConical,
  Zap,
  Package,
  Wrench,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  CircleDot,
  X,
  ChevronDown,
  ChevronRight,
  Minus,
  Plus,
} from 'lucide-react'
import { MessageBubble, Message, MessageMetadata, ToolCallRecord } from './MessageBubble'
import { PendingToolCall } from './ToolApproval'
import { SessionSidebar, Subproject } from './SessionSidebar'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Project {
  id: string
  name: string
  ready: boolean
}

interface Mention {
  type: string   // file | folder | symbol | session | core | web
  value: string
  display: string
}

interface DropdownItem {
  type: string
  value: string
  display: string
  detail?: string
}

interface SkillOption {
  skill_id: string
  display_name: string
  description: string
  subproject_id?: string | null
  has_decisions?: boolean
}

export interface ActivityEvent {
  type: string
  message?: string
  tool?: string
  params?: Record<string, unknown>
  risk?: string
  duration_ms?: number
  result_chars?: number
  tier?: number
  model?: string
  manual?: boolean
  estimated?: number
  limit?: number
  diff?: string
  from_tier?: number
  to_tier?: number
  reason?: string
  iteration?: number
  phase?: string
  text?: string
}

function isInternalTestSubproject(subproject: Subproject): boolean {
  const haystack = `${subproject.name} ${subproject.display_name} ${subproject.description}`.toLowerCase()
  return haystack.includes('example.com') || haystack.includes('created in test')
}

const MODEL_OPTIONS = [
  { value: 'auto',     label: 'Auto',       detail: 'recommended',   cost: '' },
  { value: 'local',    label: 'Local',    detail: 'qwen2.5-coder', cost: 'free' },
  { value: 'deepseek', label: 'DeepSeek', detail: 'deepseek-chat', cost: '~$0.001' },
  { value: 'sonnet',   label: 'Sonnet',   detail: 'claude-sonnet', cost: '~$0.005' },
  { value: 'opus',     label: 'Opus',     detail: 'claude-opus',  cost: '~$0.015' },
]

function generateId() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

const TOKEN_TRIM_THRESHOLD = 40_000

// ── Sub-components ────────────────────────────────────────────────────────────

function TokenBar({ tokens }: { tokens: number }) {
  const pct = Math.min(100, (tokens / TOKEN_TRIM_THRESHOLD) * 100)
  const warn = tokens >= TOKEN_TRIM_THRESHOLD
  return (
    <div className="border-b border-slate-200 bg-white px-5 py-2.5 text-2xs text-slate-600">
      <div className="flex items-center gap-3">
        <span className="label-xs">Context</span>
        <div className="h-1 flex-1 overflow-hidden rounded-sm bg-slate-100">
          <div
            className={`h-full transition-all ${warn ? 'bg-red-500' : 'bg-accent'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-medium tabular-nums text-slate-700">
          {tokens.toLocaleString()} / {TOKEN_TRIM_THRESHOLD.toLocaleString()}
        </span>
      </div>
    </div>
  )
}

const ICON_SIZE = 14

function MentionPill({ mention, onRemove }: { mention: Mention; onRemove: () => void }) {
  const Icon =
    mention.type === 'file' ? FileText :
    mention.type === 'folder' ? Folder :
    mention.type === 'symbol' ? Code2 :
    mention.type === 'session' ? MessageSquare :
    mention.type === 'core' ? Pin :
    Globe
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 py-1 text-2xs font-medium text-slate-700">
      <Icon size={12} className="shrink-0 text-slate-500" />
      <span className="truncate max-w-[160px]">{mention.display}</span>
      <button
        onClick={onRemove}
        className="ml-0.5 text-slate-400 hover:text-slate-700"
        aria-label="Remove mention"
      >
        <X size={12} />
      </button>
    </span>
  )
}

function toolIconComponent(tool: string) {
  if (tool === 'read_file') return BookOpen
  if (tool === 'search_code') return Search
  if (tool === 'edit_file' || tool === 'create_file') return PencilLine
  if (tool === 'run_tests') return FlaskConical
  if (tool === 'run_command' || tool === 'run_migration') return Zap
  if (tool === 'git_add' || tool === 'git_commit' || tool === 'git_push' || tool === 'git_branch' || tool === 'git_stash') return Package
  if (tool === 'git_status' || tool === 'git_diff' || tool === 'git_log') return BookOpen
  if (tool === 'web_fetch' || tool === 'web_check_status') return Globe
  if (tool === 'web_search') return Search
  return Wrench
}

function renderActivityIcon(event: ActivityEvent): ReactNode {
  const cn = 'shrink-0 text-slate-500'
  if (event.type === 'status') return <CircleDot size={ICON_SIZE} className={cn} />
  if (event.type === 'routing') return <ArrowRight size={ICON_SIZE} className={cn} />
  if (event.type === 'tokens') return <BarChart3 size={ICON_SIZE} className={cn} />
  if (event.type === 'tool_start' || event.type === 'tool_end' || event.type === 'tool_queued') {
    const I = toolIconComponent(event.tool || '')
    return <I size={ICON_SIZE} className={cn} />
  }
  if (event.type === 'validation') return <CheckCircle2 size={ICON_SIZE} className="shrink-0 text-emerald-600" />
  if (event.type === 'escalation') return <Zap size={ICON_SIZE} className="shrink-0 text-amber-600" />
  if (event.type === 'wiggum_iteration') return <ArrowRight size={ICON_SIZE} className={cn} />
  if (event.type === 'error') return <AlertTriangle size={ICON_SIZE} className="shrink-0 text-red-600" />
  return <CircleDot size={ICON_SIZE} className={cn} />
}

function activityText(event: ActivityEvent): string {
  if (event.type === 'status') {
    return event.message || 'Working…'
  }
  if (event.type === 'routing') {
    const tierLabels: Record<number, string> = { 1: 'Local', 2: 'DeepSeek', 3: 'Claude', 4: 'Opus' }
    const tierStr = event.tier ? `Tier ${event.tier} (${tierLabels[event.tier] || '?'})` : ''
    const manualStr = event.manual ? ' [manual]' : ''
    const modelStr = event.model ? ` — ${event.model}` : ''
    return `${tierStr}${modelStr}${manualStr}`
  }
  if (event.type === 'tokens') {
    return `~${(event.estimated || 0).toLocaleString()} tokens`
  }
  if (event.type === 'tool_start') {
    const p = event.params || {}
    const detail = (p.file_path || p.query || p.pattern || '') as string
    return `${event.tool}  ${detail ? `  ${detail}` : ''}  …`
  }
  if (event.type === 'tool_end') {
    const kb = event.result_chars ? `${Math.round(event.result_chars / 100) / 10}k chars` : ''
    return `${event.tool}  ${event.duration_ms}ms  ${kb}`
  }
  if (event.type === 'tool_queued') {
    return `${event.tool}  [queued — approval required]`
  }
  if (event.type === 'escalation') {
    return `Escalated Tier ${event.from_tier} → Tier ${event.to_tier}  ${event.reason || ''}`
  }
  if (event.type === 'wiggum_iteration') {
    return `Iteration ${event.iteration}  [${event.phase}]`
  }
  if (event.type === 'error') {
    return `Error: ${(event as { message?: string }).message || 'unknown'}`
  }
  return JSON.stringify(event)
}

function latestStatus(events: ActivityEvent[]): string {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i]
    if (ev.type === 'status') return ev.message || 'Working…'
    if (ev.type === 'tool_start') return activityText(ev)
  }
  return 'Working…'
}

function ActivityLog({
  events,
  live,
  collapsed,
  onToggle,
}: {
  events: ActivityEvent[]
  live: boolean
  collapsed: boolean
  onToggle: () => void
}) {
  if (events.length === 0 && !live) return null

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-slate-200 bg-white text-2xs">
      {/* Header */}
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between bg-slate-50 px-3.5 py-2 text-slate-600 transition-colors hover:bg-slate-100"
      >
        <span className="label-xs">Activity</span>
        <span className="flex items-center gap-2">
          {live && (
            <span className="inline-flex items-center gap-1 text-emerald-600">
              <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
              <span className="text-2xs font-medium">live</span>
            </span>
          )}
          {collapsed
            ? <ChevronRight size={14} className="text-slate-400" />
            : <ChevronDown size={14} className="text-slate-400" />}
        </span>
      </button>

      {!collapsed && (
        <div className="divide-y divide-slate-100 border-t border-slate-200 font-mono">
          {events.map((ev, i) => {
            const text = activityText(ev)
            const isRunning = live && i === events.length - 1 && ev.type === 'tool_start'
            const isError = ev.type === 'error'
            const isQueued = ev.type === 'tool_queued'

            return (
              <div
                key={i}
                className={`flex items-center gap-2.5 px-3.5 py-2 ${
                  isError ? 'text-red-700' : isQueued ? 'text-amber-700' : 'text-slate-600'
                }`}
              >
                {renderActivityIcon(ev)}
                <span className={`flex-1 truncate ${isRunning ? 'animate-pulse' : ''}`}>
                  {text}
                </span>
                {isRunning && (
                  <Loader2 size={12} className="shrink-0 animate-spin text-slate-400" />
                )}
              </div>
            )
          })}

          {live && (
            <div className="flex items-center gap-2.5 px-3.5 py-2 text-slate-400">
              <Loader2 size={12} className="shrink-0 animate-spin" />
              <span className="animate-pulse">thinking…</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main ChatWindow ───────────────────────────────────────────────────────────

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

  // Subproject
  const [subprojects, setSubprojects] = useState<Subproject[]>([])
  const [activeSubprojectId, setActiveSubprojectId] = useState<string | null>(null)
  const [availableSkills, setAvailableSkills] = useState<SkillOption[]>([])
  const [activeSkillIds, setActiveSkillIds] = useState<string[]>([])

  // Tokens / archive
  const [sessionTokens, setSessionTokens] = useState(0)
  const [archiveBanner, setArchiveBanner] = useState<string | null>(null)

  // Index warning & header indicator
  const [indexWarning, setIndexWarning] = useState<{ project: string; message: string } | null>(null)
  const [indexDismissed, setIndexDismissed] = useState(false)
  const [indexChunks, setIndexChunks] = useState<number | null>(null)

  // @ mention
  const [mentions, setMentions] = useState<Mention[]>([])
  const [mentionQuery, setMentionQuery] = useState<string | null>(null)
  const [mentionTab, setMentionTab] = useState<'files' | 'folders' | 'symbols' | 'sessions'>('files')
  const [mentionItems, setMentionItems] = useState<DropdownItem[]>([])
  const [mentionIndex, setMentionIndex] = useState(0)
  const mentionFetchRef = useRef<AbortController | null>(null)

  // Model selector
  const [modelOverride, setModelOverride] = useState<string>('auto')
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false)

  // Live activity log (in-flight, before the final message arrives)
  const [liveActivity, setLiveActivity] = useState<ActivityEvent[]>([])
  const [liveDraftText, setLiveDraftText] = useState('')
  const [activityCollapsed, setActivityCollapsed] = useState<Record<string, boolean>>({})

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const modelDropdownRef = useRef<HTMLDivElement>(null)
  const activeStreamRef = useRef<EventSource | null>(null)
  const activeRequestControllerRef = useRef<AbortController | null>(null)
  const stopRequestedRef = useRef(false)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, liveActivity])

  useEffect(() => { setSessionId(generateId()) }, [])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (modelDropdownRef.current && !modelDropdownRef.current.contains(e.target as Node)) {
        setModelDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
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
    if (!projectId) return
    fetch(`/api/subprojects?project=${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(data => setSubprojects(data.subprojects || []))
      .catch(() => {})
  }, [projectId])

  useEffect(() => {
    if (!projectId) { setAvailableSkills([]); return }
    fetch(`/api/skills?project=${encodeURIComponent(projectId)}`)
      .then(r => r.json())
      .then(data => setAvailableSkills(data.skills || []))
      .catch(() => setAvailableSkills([]))
  }, [projectId])

  useEffect(() => {
    if (projectId) {
      localStorage.setItem('claw_project', projectId)
      const savedSkills = localStorage.getItem(`claw_skills_${projectId}`)
      let parsedSkills: string[] = []
      if (savedSkills) {
        try {
          parsedSkills = JSON.parse(savedSkills)
        } catch {
          parsedSkills = []
        }
      }
      setMessages([{ id: generateId(), role: 'system', content: `Project: ${projectId}` }])
      setActiveSubprojectId(null)
      setActiveSkillIds(parsedSkills)
      setSessionTokens(0)
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId) return
    localStorage.setItem(`claw_skills_${projectId}`, JSON.stringify(activeSkillIds))
  }, [projectId, activeSkillIds])

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
            const base64 = result.split(',')[1]
            setPastedImage(base64)
            setPastedImageType(
              ['image/jpeg', 'image/png', 'image/gif', 'image/webp'].includes(item.type)
                ? item.type : 'image/png',
            )
          }
          reader.readAsDataURL(blob)
          break
        }
      }
    }
    document.addEventListener('paste', handlePaste)
    return () => document.removeEventListener('paste', handlePaste)
  }, [])

  // ── Index warning — poll /health every 30s ────────────────────────────────
  useEffect(() => {
    if (!projectId || indexDismissed) return
    let cancelled = false
    const check = async () => {
      try {
        const r = await fetch('http://localhost:8765/health')
        const data = await r.json()
        const status = data.index_status?.[projectId]
        if (!status) return
        setIndexChunks(status.chunks ?? null)
        if (status.indexed) {
          setIndexWarning(null)
        } else {
          setIndexWarning({
            project: projectId,
            message: status.watcher_active
              ? 'Indexing automatically in background...'
              : 'Run: python scripts/index_project.py --project ' + projectId + ' --force',
          })
        }
      } catch { /* ignore */ }
    }
    check()
    const timer = setInterval(() => { if (!cancelled) check() }, 30_000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [projectId, indexDismissed])

  // ── @ mention dropdown ──────────────────────────────────────────────────────

  useEffect(() => {
    if (mentionQuery === null || !projectId) { setMentionItems([]); return }
    if (mentionFetchRef.current) mentionFetchRef.current.abort()
    const ctrl = new AbortController()
    mentionFetchRef.current = ctrl
    const q = encodeURIComponent(mentionQuery)
    const headers: Record<string, string> = {}

    const fetchTab = async () => {
      try {
        if (mentionTab === 'files') {
          const r = await fetch(`http://localhost:8765/projects/${projectId}/files?q=${q}`, { headers, signal: ctrl.signal })
          const data = await r.json()
          setMentionItems((data.files || []).slice(0, 20).map((f: string) => ({
            type: 'file', value: f, display: f.split('/').pop() || f, detail: f,
          })))
        } else if (mentionTab === 'folders') {
          const r = await fetch(`http://localhost:8765/projects/${projectId}/files`, { headers, signal: ctrl.signal })
          const data = await r.json()
          const dirs = new Set<string>()
          ;(data.files || []).forEach((f: string) => { const p = f.split('/'); if (p.length > 1) dirs.add(p[0]) })
          setMentionItems(Array.from(dirs).filter(d => !mentionQuery || d.toLowerCase().includes(mentionQuery.toLowerCase())).slice(0, 20).map(d => ({ type: 'folder', value: d, display: d, detail: d + '/' })))
        } else if (mentionTab === 'symbols') {
          if (!mentionQuery) { setMentionItems([]); return }
          const r = await fetch(`http://localhost:8765/projects/${projectId}/symbols?q=${q}`, { headers, signal: ctrl.signal })
          const data = await r.json()
          setMentionItems((data.symbols || []).slice(0, 20).map((s: { name: string; file: string; type: string }) => ({
            type: 'symbol', value: s.name, display: s.name, detail: `${s.type} in ${s.file}`,
          })))
        } else if (mentionTab === 'sessions') {
          const r = await fetch(`/api/sessions?project=${projectId}`, { signal: ctrl.signal })
          const data = await r.json()
          setMentionItems((data.sessions || []).slice(0, 20).map((s: { session_id: string; title?: string; preview?: string; created_at?: string }) => ({
            type: 'session',
            value: s.session_id,
            display: s.title || s.session_id.slice(0, 12),
            detail: s.preview || s.session_id.slice(0, 12),
          })))
        }
        setMentionIndex(0)
      } catch { /* aborted or failed */ }
    }
    fetchTab()
  }, [mentionQuery, mentionTab, projectId])

  const closeMentionDropdown = useCallback(() => { setMentionQuery(null); setMentionItems([]) }, [])

  const selectMentionItem = useCallback((item: DropdownItem) => {
    setMentions(prev => {
      if (prev.some(m => m.type === item.type && m.value === item.value)) return prev
      return [...prev, { type: item.type, value: item.value, display: item.display }]
    })
    setInput(prev => { const atIdx = prev.lastIndexOf('@'); return atIdx >= 0 ? prev.slice(0, atIdx) : prev })
    closeMentionDropdown()
    textareaRef.current?.focus()
  }, [closeMentionDropdown])

  // ── Input handlers ──────────────────────────────────────────────────────────

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setInput(val)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
    const cursor = e.target.selectionStart ?? val.length
    const atMatch = val.slice(0, cursor).match(/@(\S*)$/)
    if (atMatch) setMentionQuery(atMatch[1])
    else closeMentionDropdown()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionQuery !== null && mentionItems.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIndex(i => Math.min(i + 1, mentionItems.length - 1)); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMentionIndex(i => Math.max(i - 1, 0)); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); if (mentionItems[mentionIndex]) selectMentionItem(mentionItems[mentionIndex]); return }
      if (e.key === 'Escape') { closeMentionDropdown(); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit() }
  }

  const newSession = useCallback(() => {
    // Close any active stream
    if (activeStreamRef.current) { activeStreamRef.current.close(); activeStreamRef.current = null }
    if (activeRequestControllerRef.current) {
      activeRequestControllerRef.current.abort()
      activeRequestControllerRef.current = null
    }
    stopRequestedRef.current = false
    setSessionId(generateId())
    setSessionCost(0); setLocalCalls(0); setApiCalls(0); setSessionTokens(0)
    setArchiveBanner(null); setMentions([]); setLiveActivity([]); setLiveDraftText(''); setActiveSkillIds([])
    setMessages([{ id: generateId(), role: 'system', content: `New session — project: ${projectId}` }])
  }, [projectId])

  const stopGeneration = useCallback(() => {
    stopRequestedRef.current = true
    void fetch('/api/chat/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId, session_id: sessionId }),
    }).catch(() => {})
    if (activeStreamRef.current) {
      activeStreamRef.current.close()
      activeStreamRef.current = null
    }
    if (activeRequestControllerRef.current) {
      activeRequestControllerRef.current.abort()
      activeRequestControllerRef.current = null
    }
    setLoading(false)
    setLiveActivity([])
    setLiveDraftText('')
  }, [projectId, sessionId])

  // ── Streaming send ──────────────────────────────────────────────────────────

  const sendMessage = useCallback(async (
    content: string,
    toolApproval?: Record<string, unknown>,
  ) => {
    if (!projectId) return
    stopRequestedRef.current = false
    setLoading(true)
    setLiveActivity([{ type: 'status', message: 'Preparing request…' }])
    setLiveDraftText('')

    const imgBase64 = pastedImage
    const imgType = pastedImageType
    const imgPreview = imgBase64 ? `data:${imgType};base64,${imgBase64}` : undefined

    if (content && !toolApproval) {
      setMessages(prev => [...prev, { id: generateId(), role: 'user', content, imagePreview: imgPreview }])
    }
    setPastedImage(null)

    const currentMentions = mentions
    setMentions([])
    const currentSubprojectId = activeSubprojectId || undefined
    const currentOverride = modelOverride === 'auto' ? undefined : modelOverride
    const currentSkillIds = activeSkillIds

    // Tool approvals use POST /chat (non-streaming — state machine)
    if (toolApproval) {
      try {
        const controller = new AbortController()
        activeRequestControllerRef.current = controller
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            content: content || '',
            project_id: projectId, session_id: sessionId, channel: 'web',
            subproject_id: currentSubprojectId,
            skill_ids: currentSkillIds,
            tool_approval: toolApproval,
          }),
        })
        const data = await res.json()
        _handleChatResponse(data)
      } catch (err) {
        if ((err as Error).name !== 'AbortError') _handleNetworkError()
      } finally {
        activeRequestControllerRef.current = null
        setLoading(false)
      }
      return
    }

    if (imgBase64) {
      setLiveActivity([{
        type: 'status',
        message: `Analysing image with ${(currentOverride || 'auto').replace(/^./, c => c.toUpperCase())}…`,
      }])
      await _fallbackPost(
        content,
        currentMentions,
        currentOverride,
        currentSubprojectId,
        currentSkillIds,
        imgBase64,
        imgType,
      )
      return
    }

    // Normal messages — use SSE stream
    const activityLog: ActivityEvent[] = []
    const msgId = generateId()
    let streamCompleted = false

    const params = new URLSearchParams({
      project: projectId,
      session_id: sessionId,
      message: content || 'What do you see in this screenshot?',
    })
    if (currentMentions.length > 0) params.set('mentions', JSON.stringify(currentMentions))
    if (currentSubprojectId) params.set('subproject_id', currentSubprojectId)
    if (currentOverride) params.set('model_override', currentOverride)
    if (currentSkillIds.length > 0) params.set('skill_ids', JSON.stringify(currentSkillIds))

    // Close any existing stream
    if (activeStreamRef.current) activeStreamRef.current.close()

    const streamUrl = `/api/chat/stream?${params.toString()}`
    let es: EventSource

    try {
      es = new EventSource(streamUrl)
      activeStreamRef.current = es
    } catch {
      // EventSource not available — fall back to POST /chat
      await _fallbackPost(
        content,
        currentMentions,
        currentOverride,
        currentSubprojectId,
      )
      return
    }

    es.onmessage = (e) => {
      let event: ActivityEvent & { type: string; response?: string; cost_usd?: number; model_used?: string; metadata?: Record<string, unknown>; executed_tool_calls?: ToolCallRecord[]; pending_tool_call?: PendingToolCall | null; message?: string }
      try { event = JSON.parse(e.data) } catch { return }

      if (event.type === 'done') {
        streamCompleted = true
        es.close()
        activeStreamRef.current = null
        setLoading(false)
        setLiveActivity([])
        setLiveDraftText('')
        return
      }

      if (event.type === 'response_delta') {
        setLiveDraftText(prev => prev + (event.text || ''))
        return
      }

      if (event.type === 'complete') {
        streamCompleted = true
        es.close()
        activeStreamRef.current = null
        setLoading(false)
        setLiveActivity([])
        setLiveDraftText('')

        const isLocal = (event.model_used || '').toLowerCase().includes('qwen')
        if (isLocal) setLocalCalls(c => c + 1)
        else if (event.model_used) { setApiCalls(c => c + 1); setSessionCost(c => c + (event.cost_usd || 0)) }

        const meta = (event.metadata || {}) as MessageMetadata & Record<string, unknown>
        if (meta.session_archived) {
          setArchiveBanner((meta.archive_summary as string) || 'Session archived')
          newSession()
        }

        setMessages(prev => [...prev, {
          id: msgId,
          role: 'assistant',
          content: event.response || '(no response)',
          modelUsed: event.model_used,
          costUsd: event.cost_usd,
          modelRouting: (meta.model_routing as string) || 'auto',
          metadata: meta,
          pendingToolCall: event.pending_tool_call || null,
          toolCalls: (event.executed_tool_calls || []) as ToolCallRecord[],
          activityLog: activityLog.length > 0 ? ([...activityLog] as unknown[]) : undefined,
        }])
        return
      }

      if (event.type === 'error') {
        streamCompleted = true
        es.close()
        activeStreamRef.current = null
        setLoading(false)
        setLiveActivity([])
        setLiveDraftText('')
        setMessages(prev => [...prev, {
          id: generateId(), role: 'assistant',
          content: `Error: ${event.message || 'Stream error'}`,
        }])
        return
      }

      // Live activity event
      activityLog.push(event)
      setLiveActivity([...activityLog])
    }

    es.onerror = () => {
      es.close()
      activeStreamRef.current = null
      if (!streamCompleted && !stopRequestedRef.current) {
        setLiveActivity([])
        setLiveDraftText('')
        void _fallbackPost(
          content,
          currentMentions,
          currentOverride,
          currentSubprojectId,
          currentSkillIds,
        )
      }
    }
  }, [projectId, sessionId, pastedImage, pastedImageType, mentions, modelOverride, activeSubprojectId, activeSkillIds, newSession])

  const _handleChatResponse = useCallback((data: Record<string, unknown>) => {
    if (data.error) {
      setMessages(prev => [...prev, { id: generateId(), role: 'assistant', content: `Error: ${data.error}` }])
      return
    }
    const isLocal = ((data.model_used as string) || '').toLowerCase().includes('qwen')
    if (isLocal) setLocalCalls(c => c + 1)
    else if (data.model_used) { setApiCalls(c => c + 1); setSessionCost(c => c + ((data.cost_usd as number) || 0)) }
    if (data.tokens_used) setSessionTokens(t => t + (data.tokens_used as number))
    const meta = ((data.metadata as MessageMetadata & Record<string, unknown>) || {})
    if (meta.session_archived) {
      setArchiveBanner((meta.archive_summary as string) || 'Session archived')
      newSession()
    }
    setMessages(prev => [...prev, {
      id: generateId(), role: 'assistant',
      content: (data.content as string) || '(no response)',
      modelUsed: data.model_used as string,
      costUsd: data.cost_usd as number,
      modelRouting: (meta.model_routing as string) || 'auto',
      metadata: meta,
      pendingToolCall: (data.pending_tool_call as PendingToolCall) || null,
      toolCalls: ((data.tool_calls as ToolCallRecord[]) || []),
    }])
  }, [newSession])

  const _fallbackPost = useCallback(async (
    content: string,
    currentMentions: Mention[],
    currentOverride: string | undefined,
    currentSubprojectId?: string,
    currentSkillIds: string[] = [],
    imageBase64?: string,
    imageMediaType?: string,
  ) => {
    const controller = new AbortController()
    activeRequestControllerRef.current = controller
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          content: content || '',
          project_id: projectId, session_id: sessionId, channel: 'web',
          mentions: currentMentions,
          model_override: currentOverride,
          subproject_id: currentSubprojectId,
          skill_ids: currentSkillIds,
          image_base64: imageBase64,
          image_media_type: imageMediaType,
        }),
      })
      const data = await res.json()
      _handleChatResponse(data)
    } catch (err) {
      if ((err as Error).name !== 'AbortError') _handleNetworkError()
    } finally {
      activeRequestControllerRef.current = null
      setLoading(false)
      setLiveDraftText('')
    }
  }, [projectId, sessionId, _handleChatResponse])

  const _handleNetworkError = useCallback(() => {
    setMessages(prev => [...prev, {
      id: generateId(), role: 'assistant',
      content: `Error: network error — API unreachable. Restart uvicorn and refresh.`,
    }])
  }, [])

  const handleSubmit = () => {
    const text = input.trim()
    if ((!text && !pastedImage) || loading) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    sendMessage(text || 'What do you see in this screenshot?')
  }

  const handleApprove = useCallback((toolCall: PendingToolCall) => {
    sendMessage('', { tool_call_id: toolCall.tool_call_id, tool_name: toolCall.tool_name, input: toolCall.input, approved: true })
  }, [sendMessage])

  const handleReject = useCallback((toolCall: PendingToolCall) => {
    sendMessage('', { tool_call_id: toolCall.tool_call_id, tool_name: toolCall.tool_name, input: toolCall.input, approved: false })
  }, [sendMessage])

  const activeSubproject = subprojects.find(sp => sp.id === activeSubprojectId)
  const visibleSubprojects = subprojects.filter(sp => !isInternalTestSubproject(sp))
  const selectedModel = MODEL_OPTIONS.find(m => m.value === modelOverride) || MODEL_OPTIONS[0]
  const visibleSkills = availableSkills.filter(skill => !isInternalTestSubproject({
    id: skill.skill_id,
    name: skill.skill_id,
    display_name: skill.display_name,
    description: skill.description || '',
  } as Subproject))
  const loadSession = useCallback(async (sid: string) => {
    setSessionId(sid)
    setSessionTokens(0)
    setArchiveBanner(null)
    setLiveActivity([])

    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}?project=${encodeURIComponent(projectId)}`)
      const data = await res.json()
      if (data.error) {
        setMessages([{ id: generateId(), role: 'system', content: `Could not load session: ${sid}` }])
        return
      }
      setActiveSubprojectId(data.subproject_id || null)
      setActiveSkillIds((data.skill_ids || []) as string[])
      setMessages((data.messages || []).map((msg: { role: 'user' | 'assistant' | 'system'; content: string; model_used?: string; cost_usd?: number }) => ({
        id: generateId(),
        role: msg.role,
        content: msg.content,
        modelUsed: msg.model_used,
        costUsd: msg.cost_usd,
      })))
    } catch {
      setMessages([{ id: generateId(), role: 'system', content: `Could not load session: ${sid}` }])
    }
  }, [projectId])

  return (
    <div className="flex h-full min-h-0 bg-slate-50 text-slate-900">
      <SessionSidebar
        projectId={projectId}
        activeSessionId={sessionId}
        activeSubprojectId={activeSubprojectId}
        onSessionSelect={loadSession}
        onSubprojectChange={(spId) => setActiveSubprojectId(spId)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header */}
        <header className="flex-shrink-0 border-b border-slate-200 bg-white px-5 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {/* Brand + selectors — left group */}
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-900">
                {/* Stacked-stone mark — matches the PWA icon */}
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-white">
                  <rect x="4"   y="18" width="16" height="3" rx="1.2"/>
                  <rect x="5.5" y="13" width="13" height="3" rx="1.2"/>
                  <rect x="7"   y="8"  width="10" height="3" rx="1.2"/>
                  <rect x="9"   y="3"  width="6"  height="3" rx="1.2"/>
                </svg>
              </div>
              <div className="leading-tight">
                <div className="text-sm font-semibold tracking-tight text-slate-900">Cairn</div>
                <div className="text-2xs text-slate-500">Sovereign agent · NBNE</div>
              </div>
            </div>

            <div className="h-6 w-px bg-slate-200" aria-hidden />

            <div className="flex items-center gap-2">
              <label className="label-xs">Project</label>
              <select
                value={projectId}
                onChange={e => setProjectId(e.target.value)}
                className="focus-ring rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800"
              >
                {projects.length === 0 && <option value="">No projects</option>}
                {projects.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
              </select>
            </div>

            {visibleSubprojects.length > 0 && (
              <div className="flex items-center gap-2">
                <label className="label-xs">Client</label>
                <select
                  value={activeSubprojectId || ''}
                  onChange={e => setActiveSubprojectId(e.target.value || null)}
                  className="focus-ring rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-800"
                >
                  <option value="">All clients</option>
                  {visibleSubprojects.map(sp => <option key={sp.id} value={sp.id}>{sp.display_name}</option>)}
                </select>
              </div>
            )}

            {visibleSkills.length > 0 && (
              <div className="flex min-w-[220px] flex-wrap items-center gap-1.5">
                <label className="label-xs mr-1">Skills</label>
                {visibleSkills.slice(0, 6).map(skill => {
                  const active = activeSkillIds.includes(skill.skill_id)
                  return (
                    <button
                      key={skill.skill_id}
                      type="button"
                      onClick={() => setActiveSkillIds(prev => (
                        prev.includes(skill.skill_id)
                          ? prev.filter(id => id !== skill.skill_id)
                          : [...prev, skill.skill_id].slice(0, 2)
                      ))}
                      className={`focus-ring rounded-md border px-2 py-1 text-2xs font-medium transition-colors ${
                        active
                          ? 'border-slate-900 bg-slate-900 text-white'
                          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900'
                      }`}
                      title={skill.description}
                    >
                      {skill.display_name}
                    </button>
                  )
                })}
                {activeSkillIds.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setActiveSkillIds([])}
                    className="focus-ring rounded-md px-2 py-1 text-2xs font-medium text-slate-500 hover:text-slate-900"
                  >
                    Clear
                  </button>
                )}
              </div>
            )}

            {/* Right group — metrics + new chat */}
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <div className="chip" title="Session spend">
                <span className="label-xs">$</span>
                <span className="chip-value">{sessionCost.toFixed(4)}</span>
              </div>
              <div className="chip" title="Local / API calls">
                <span className="chip-value">{localCalls}</span>
                <span className="text-slate-400">/</span>
                <span className="chip-value">{apiCalls}</span>
              </div>
              <div className="chip font-mono" title={`Session ${sessionId}`}>
                <span className="chip-value">{sessionId.slice(0, 8)}</span>
              </div>
              <div
                className={`chip ${
                  indexChunks === null ? 'text-slate-400'
                  : indexChunks > 0 ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                  : 'border-amber-200 bg-amber-50 text-amber-700'
                }`}
                title={indexChunks !== null ? `${indexChunks} indexed chunks` : 'Index status unknown'}
              >
                <span className="label-xs">idx</span>
                <span className="chip-value">{indexChunks === null ? '—' : indexChunks}</span>
              </div>
              <button
                onClick={newSession}
                className="focus-ring ml-1 inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-slate-700"
              >
                <Plus size={14} />
                New chat
              </button>
            </div>
          </div>
        </header>

        {sessionTokens > 0 && <TokenBar tokens={sessionTokens} />}

        {archiveBanner && (
          <div className="flex items-center justify-between border-b border-emerald-200 bg-emerald-50 px-5 py-2 text-xs text-emerald-800">
            <span className="flex items-center gap-2">
              <CheckCircle2 size={14} className="shrink-0" />
              Session archived — summary added to context
            </span>
            <button
              onClick={() => setArchiveBanner(null)}
              className="ml-4 text-emerald-500 hover:text-emerald-700"
              aria-label="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {indexWarning && !indexDismissed && (
          <div className="flex items-center justify-between border-b border-amber-200 bg-amber-50 px-5 py-2 text-xs text-amber-800">
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} className="shrink-0" />
              <span>
                <span className="font-semibold">Project {indexWarning.project} has no indexed content.</span>{' '}
                <span className="text-amber-700">Responses may be less accurate.</span>{' '}
                <span className="text-amber-600">{indexWarning.message}</span>
              </span>
            </div>
            <button
              onClick={() => setIndexDismissed(true)}
              className="ml-4 text-amber-500 hover:text-amber-700"
              aria-label="Dismiss"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {/* Messages */}
        <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50 px-6 py-5 md:px-10 md:py-7">
          {messages.map((msg, msgIdx) => (
            <div key={msg.id}>
              <MessageBubble
                message={msg}
                onApprove={handleApprove}
                onReject={handleReject}
              />
              {/* Completed activity log collapsed below the response */}
              {msg.activityLog && msg.activityLog.length > 0 && (
                <ActivityLog
                  events={msg.activityLog as unknown as ActivityEvent[]}
                  live={false}
                  collapsed={activityCollapsed[msg.id] !== false}
                  onToggle={() => setActivityCollapsed(prev => ({ ...prev, [msg.id]: !(prev[msg.id] !== false) }))}
                />
              )}
            </div>
          ))}

          {/* Live activity log while streaming */}
          {loading && (
            <>
              <MessageBubble
                message={{
                  id: 'pending-assistant',
                  role: 'assistant',
                  content: liveDraftText || latestStatus(liveActivity),
                }}
              />
              <ActivityLog
                events={liveActivity}
                live={true}
                collapsed={false}
                onToggle={() => {}}
              />
            </>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="flex-shrink-0 border-t border-slate-200 bg-white px-5 py-3">
          {pastedImage && (
            <div className="relative mb-2 inline-block">
              <img
                src={`data:${pastedImageType};base64,${pastedImage}`}
                alt="Pasted screenshot"
                className="max-h-28 rounded-md border border-slate-200"
              />
              <button
                onClick={() => setPastedImage(null)}
                className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-subtle hover:text-slate-900"
                aria-label="Remove image"
              >
                <X size={12} />
              </button>
            </div>
          )}

          {mentions.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {mentions.map((m, i) => (
                <MentionPill
                  key={`${m.type}:${m.value}`}
                  mention={m}
                  onRemove={() => setMentions(prev => prev.filter((_, j) => j !== i))}
                />
              ))}
            </div>
          )}

          {/* @ mention dropdown */}
          {mentionQuery !== null && (
            <div className="relative mb-2">
              <div className="absolute bottom-0 left-0 right-0 z-50 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-pop">
                <div className="flex border-b border-slate-200 bg-slate-50">
                  {(['files', 'folders', 'symbols', 'sessions'] as const).map(tab => (
                    <button
                      key={tab}
                      onClick={() => { setMentionTab(tab); setMentionIndex(0) }}
                      className={`flex-1 py-2 text-2xs font-medium capitalize transition-colors ${
                        mentionTab === tab
                          ? 'border-b-2 border-slate-900 bg-white text-slate-900'
                          : 'text-slate-500 hover:text-slate-900'
                      }`}
                    >
                      {tab}
                    </button>
                  ))}
                </div>
                {mentionItems.length === 0 ? (
                  <div className="px-3 py-3 text-2xs italic text-slate-500">
                    {mentionQuery ? `No ${mentionTab} matching "${mentionQuery}"` : `Type to search ${mentionTab}…`}
                  </div>
                ) : (
                  <div className="max-h-48 overflow-y-auto">
                    {mentionItems.map((item, i) => (
                      <button
                        key={`${item.type}:${item.value}`}
                        onClick={() => selectMentionItem(item)}
                        className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
                          i === mentionIndex
                            ? 'bg-slate-100 text-slate-900'
                            : 'text-slate-700 hover:bg-slate-50'
                        }`}
                      >
                        <span className="truncate font-medium">{item.display}</span>
                        {item.detail && (
                          <span className="ml-auto max-w-[40%] shrink-0 truncate font-mono text-2xs text-slate-400">
                            {item.detail}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                )}
                <div className="border-t border-slate-200 bg-slate-50 px-3 py-1.5 text-2xs text-slate-400">
                  ↑↓ navigate · ↵ add · esc close
                </div>
              </div>
            </div>
          )}

          <div className="flex items-end gap-2 rounded-lg border border-slate-200 bg-white p-2 focus-within:border-slate-400">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder={projectId ? (activeSubproject ? `Ask about ${activeSubproject.display_name}…` : `Ask about ${projectId}…`) : 'Select a project first'}
              disabled={!projectId || loading}
              rows={1}
              className="min-h-[36px] max-h-[120px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-slate-900 outline-none placeholder:text-slate-400 disabled:opacity-50"
            />

            {/* Model selector */}
            <div ref={modelDropdownRef} className="relative shrink-0">
              <button
                onClick={() => setModelDropdownOpen(o => !o)}
                className={`focus-ring inline-flex h-9 items-center gap-1 rounded-md border px-2.5 text-xs transition-colors ${
                  modelOverride !== 'auto'
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
                }`}
                title="Model override"
              >
                {selectedModel.label}
                <ChevronDown size={12} className="opacity-70" />
              </button>
              {modelDropdownOpen && (
                <div className="absolute bottom-full right-0 z-50 mb-2 w-56 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-pop">
                  {MODEL_OPTIONS.map(opt => (
                    <button
                      key={opt.value}
                      onClick={() => { setModelOverride(opt.value); setModelDropdownOpen(false) }}
                      className={`flex w-full items-center justify-between px-3 py-2 text-left text-xs transition-colors ${
                        modelOverride === opt.value
                          ? 'bg-slate-100 text-slate-900'
                          : 'text-slate-700 hover:bg-slate-50'
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        {modelOverride === opt.value
                          ? <CircleDot size={12} className="text-slate-900" />
                          : <Minus size={12} className="rotate-90 text-slate-300" />
                        }
                        <span className="font-medium">{opt.label}</span>
                        <span className="text-2xs text-slate-400">{opt.detail}</span>
                      </span>
                      {opt.cost && <span className="ml-2 shrink-0 font-mono text-2xs text-slate-400">{opt.cost}</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <button
              onClick={handleSubmit}
              disabled={(!input.trim() && !pastedImage) || loading || !projectId}
              className="focus-ring h-9 shrink-0 rounded-md bg-slate-900 px-4 text-xs font-medium text-white transition-colors hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Send
            </button>
            {loading && (
              <button
                onClick={stopGeneration}
                className="focus-ring h-9 shrink-0 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-red-600 transition-colors hover:border-red-300 hover:bg-red-50"
              >
                Stop
              </button>
            )}
          </div>
          <p className="mt-1.5 px-0.5 text-2xs text-slate-400">
            ↵ send · ⇧↵ newline · @ pin context
          </p>
        </div>
      </div>
    </div>
  )
}
