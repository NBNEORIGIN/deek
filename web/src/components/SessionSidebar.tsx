'use client'

import { useState, useEffect, useCallback } from 'react'
import { ChevronRight, ChevronDown, X, Plus, PanelLeftClose, PanelLeftOpen, Folder, Archive } from 'lucide-react'

export interface Subproject {
  id: string
  project_id: string
  name: string
  display_name: string
  description: string
  created_at: string
}

export interface SessionEntry {
  session_id: string
  started_at: string | null
  last_message_at: string | null
  message_count: number
  subproject_id: string | null
  archived: boolean
  title?: string
  preview?: string
}

export interface SessionSidebarProps {
  projectId: string
  activeSessionId: string
  activeSubprojectId: string | null
  onSessionSelect: (sessionId: string) => void
  onSubprojectChange: (subprojectId: string | null) => void
}

function formatSessionTime(ts: string | null): string {
  if (!ts) return '—'
  const d = new Date(ts.replace(' ', 'T') + 'Z')
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffH = diffMs / 3_600_000
  if (diffH < 1) return `${Math.round(diffMs / 60_000)}m ago`
  if (diffH < 24) return `${Math.round(diffH)}h ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function sessionMeta(s: SessionEntry): string {
  const time = formatSessionTime(s.last_message_at)
  return `${time} · ${s.message_count} msg${s.message_count !== 1 ? 's' : ''}`
}

function sessionTitle(s: SessionEntry): string {
  return s.title || s.preview || 'New chat'
}

function isInternalTestSubproject(subproject: Subproject): boolean {
  const haystack = `${subproject.name} ${subproject.display_name} ${subproject.description}`.toLowerCase()
  return haystack.includes('example.com') || haystack.includes('created in test')
}

interface NewSubprojectFormProps {
  projectId: string
  onCreated: (sp: Subproject) => void
  onCancel: () => void
}

function NewSubprojectForm({ projectId, onCreated, onCancel }: NewSubprojectFormProps) {
  const [name, setName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !displayName.trim()) return
    setSaving(true)
    try {
      const r = await fetch('/api/subprojects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project: projectId,
          name: name.trim(),
          display_name: displayName.trim(),
          description: description.trim(),
        }),
      })
      if (r.ok) {
        const sp = await r.json()
        onCreated(sp)
      }
    } finally {
      setSaving(false)
    }
  }

  const inputCls = 'mb-2 w-full rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-800 focus-ring'

  return (
    <form
      onSubmit={handleSubmit}
      className="mx-2 my-2 rounded-md border border-slate-200 bg-white p-2.5"
    >
      <input
        autoFocus
        placeholder="name (e.g. demnurse.nbne.uk)"
        value={name}
        onChange={e => setName(e.target.value)}
        className={inputCls}
      />
      <input
        placeholder="display name"
        value={displayName}
        onChange={e => setDisplayName(e.target.value)}
        className={inputCls}
      />
      <input
        placeholder="description (optional)"
        value={description}
        onChange={e => setDescription(e.target.value)}
        className={inputCls}
      />
      <div className="flex gap-1">
        <button
          type="submit"
          disabled={saving || !name.trim() || !displayName.trim()}
          className="focus-ring flex-1 rounded-md bg-slate-900 py-1.5 text-2xs font-medium text-white hover:bg-slate-700 disabled:opacity-40"
        >
          {saving ? 'Saving…' : 'Create'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="focus-ring rounded-md border border-slate-200 px-2 py-1.5 text-2xs text-slate-500 hover:bg-slate-50"
          aria-label="Cancel"
        >
          <X size={12} />
        </button>
      </div>
    </form>
  )
}

interface SubprojectFolderProps {
  subproject: Subproject
  sessions: SessionEntry[]
  activeSessionId: string
  onSessionSelect: (id: string) => void
  isSelected: boolean
  onSelect: () => void
}

function SubprojectFolder({
  subproject,
  sessions,
  activeSessionId,
  onSessionSelect,
  isSelected,
  onSelect,
}: SubprojectFolderProps) {
  const [expanded, setExpanded] = useState(isSelected)

  useEffect(() => {
    if (isSelected) setExpanded(true)
  }, [isSelected])

  return (
    <div className="mb-0.5">
      <button
        onClick={() => {
          setExpanded(e => !e)
          onSelect()
        }}
        className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-slate-100 ${
          isSelected ? 'bg-white text-slate-900' : 'text-slate-700'
        }`}
      >
        {expanded ? <ChevronDown size={12} className="shrink-0 text-slate-400" /> : <ChevronRight size={12} className="shrink-0 text-slate-400" />}
        <Folder size={12} className="shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{subproject.display_name}</span>
          {subproject.description && (
            <span className="block truncate text-2xs text-slate-500">{subproject.description}</span>
          )}
        </span>
        {sessions.length > 0 && (
          <span className="shrink-0 rounded-sm bg-slate-200 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-slate-600">
            {sessions.length}
          </span>
        )}
      </button>

      {expanded && (
        <div className="ml-3.5 border-l border-slate-200 pl-1.5">
          {sessions.length === 0 && (
            <p className="px-2 py-1 text-2xs text-slate-400">No chats yet</p>
          )}
          {sessions.map(s => (
            <button
              key={s.session_id}
              onClick={() => onSessionSelect(s.session_id)}
              className={`flex w-full items-start gap-1.5 rounded-md px-2 py-1.5 text-left text-2xs transition-colors hover:bg-slate-100 ${
                s.session_id === activeSessionId
                  ? 'bg-white text-slate-900 ring-1 ring-slate-200'
                  : 'text-slate-600'
              }`}
            >
              {s.archived && <Archive size={10} className="mt-0.5 shrink-0 text-slate-400" />}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-slate-800">{sessionTitle(s)}</span>
                <span className="block truncate text-2xs text-slate-400">{sessionMeta(s)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function SessionSidebar({
  projectId,
  activeSessionId,
  activeSubprojectId,
  onSessionSelect,
  onSubprojectChange,
}: SessionSidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [subprojects, setSubprojects] = useState<Subproject[]>([])
  const [sessions, setSessions] = useState<SessionEntry[]>([])
  const [showNewForm, setShowNewForm] = useState(false)

  const loadData = useCallback(async () => {
    if (!projectId) return
    try {
      const [spRes, sessRes] = await Promise.all([
        fetch(`/api/subprojects?project=${encodeURIComponent(projectId)}`),
        fetch(`/api/sessions?project=${encodeURIComponent(projectId)}`),
      ])
      if (spRes.ok) {
        const d = await spRes.json()
        setSubprojects(d.subprojects || [])
      }
      if (sessRes.ok) {
        const d = await sessRes.json()
        setSessions(d.sessions || [])
      }
    } catch {
      // silently ignore network errors
    }
  }, [projectId])

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 30_000)
    return () => clearInterval(interval)
  }, [loadData])

  const handleSubprojectCreated = (sp: Subproject) => {
    setSubprojects(prev => [...prev, sp])
    setShowNewForm(false)
    onSubprojectChange(sp.id)
  }

  const sessionsForSubproject = (spId: string) =>
    sessions.filter(s => s.subproject_id === spId)

  const unscopedSessions = sessions.filter(s => !s.subproject_id)
  const visibleSubprojects = subprojects.filter(sp => {
    if (!isInternalTestSubproject(sp)) return true
    return sessionsForSubproject(sp.id).length > 0 || activeSubprojectId === sp.id
  })

  if (collapsed) {
    return (
      <div className="flex w-10 flex-shrink-0 flex-col items-center border-r border-slate-200 bg-slate-50 py-3">
        <button
          onClick={() => setCollapsed(false)}
          className="focus-ring rounded-md p-1.5 text-slate-400 hover:bg-white hover:text-slate-700"
          title="Expand sidebar"
          aria-label="Expand sidebar"
        >
          <PanelLeftOpen size={14} />
        </button>
      </div>
    )
  }

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-slate-50">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-3">
        <div className="min-w-0">
          <div className="label-xs">Project</div>
          <div className="mt-0.5 truncate text-xs font-semibold text-slate-900">
            {projectId || 'No project'}
          </div>
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="focus-ring rounded-md p-1.5 text-slate-400 hover:bg-white hover:text-slate-700"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose size={14} />
        </button>
      </div>

      {/* Session tree */}
      <div className="flex-1 overflow-y-auto px-1.5 py-2">
        {/* Subproject folders */}
        {visibleSubprojects.map(sp => (
          <SubprojectFolder
            key={sp.id}
            subproject={sp}
            sessions={sessionsForSubproject(sp.id)}
            activeSessionId={activeSessionId}
            onSessionSelect={onSessionSelect}
            isSelected={activeSubprojectId === sp.id}
            onSelect={() => onSubprojectChange(sp.id)}
          />
        ))}

        {/* New subproject */}
        {showNewForm ? (
          <NewSubprojectForm
            projectId={projectId}
            onCreated={handleSubprojectCreated}
            onCancel={() => setShowNewForm(false)}
          />
        ) : (
          <button
            onClick={() => setShowNewForm(true)}
            className="focus-ring mx-0.5 mt-2 flex w-[calc(100%-0.25rem)] items-center gap-1.5 rounded-md border border-dashed border-slate-300 px-2 py-1.5 text-left text-2xs font-medium text-slate-500 transition-colors hover:border-slate-400 hover:bg-white hover:text-slate-900"
          >
            <Plus size={12} />
            New client workspace
          </button>
        )}

        {/* Unscoped sessions */}
        {unscopedSessions.length > 0 && (
          <div className="mt-3 border-t border-slate-200 pt-2">
            <p className="label-xs px-2 py-1">Recent chats</p>
            {unscopedSessions.map(s => (
              <button
                key={s.session_id}
                onClick={() => onSessionSelect(s.session_id)}
                className={`mx-0.5 flex w-[calc(100%-0.25rem)] items-start gap-1.5 rounded-md px-2 py-1.5 text-left text-2xs transition-colors hover:bg-white ${
                  s.session_id === activeSessionId
                    ? 'bg-white text-slate-900 ring-1 ring-slate-200'
                    : 'text-slate-600'
                }`}
              >
                {s.archived && <Archive size={10} className="mt-0.5 shrink-0 text-slate-400" />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-800">{sessionTitle(s)}</span>
                  <span className="block truncate text-2xs text-slate-400">{sessionMeta(s)}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
