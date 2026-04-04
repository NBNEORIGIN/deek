'use client'

import { useEffect, useState, useCallback } from 'react'
import type { SessionSummary } from '@/types/chat'
import SessionItem from './SessionItem'

interface ChatHistorySidebarProps {
  isOpen: boolean
  onClose: () => void
  currentSessionId: string | null
  onSelectSession: (sessionId: string) => void
  onNewChat: () => void
}

function groupByTime(sessions: SessionSummary[]): Record<string, SessionSummary[]> {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)
  const weekAgo = new Date(today.getTime() - 7 * 86400000)

  const groups: Record<string, SessionSummary[]> = {}

  for (const s of sessions) {
    const d = new Date(s.last_message_at)
    let label: string
    if (d >= today) label = 'Today'
    else if (d >= yesterday) label = 'Yesterday'
    else if (d >= weekAgo) label = 'Previous 7 days'
    else label = 'Older'

    if (!groups[label]) groups[label] = []
    groups[label].push(s)
  }

  return groups
}

const GROUP_ORDER = ['Today', 'Yesterday', 'Previous 7 days', 'Older']

export default function ChatHistorySidebar({
  isOpen,
  onClose,
  currentSessionId,
  onSelectSession,
  onNewChat,
}: ChatHistorySidebarProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [search, setSearch] = useState('')

  const fetchSessions = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/chat/sessions')
      if (res.ok) {
        const data = await res.json()
        setSessions(data.sessions ?? [])
      }
    } catch {
      // silently fail
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) fetchSessions()
  }, [isOpen, fetchSessions])

  const handleRename = useCallback(async (sessionId: string, title: string) => {
    try {
      await fetch(`/api/chat/sessions/${sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      setSessions((prev) =>
        prev.map((s) => (s.session_id === sessionId ? { ...s, title } : s))
      )
    } catch { /* silently fail */ }
  }, [])

  const handleArchive = useCallback(async (sessionId: string) => {
    try {
      await fetch(`/api/chat/sessions/${sessionId}/archive`, { method: 'POST' })
      setSessions((prev) =>
        prev.map((s) => (s.session_id === sessionId ? { ...s, archived: true } : s))
      )
    } catch { /* silently fail */ }
  }, [])

  const handleDelete = useCallback(async (sessionId: string) => {
    try {
      await fetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' })
      setSessions((prev) => prev.filter((s) => s.session_id !== sessionId))
    } catch { /* silently fail */ }
  }, [])

  // Filter sessions
  const filtered = sessions.filter((s) => {
    if (!showArchived && s.archived) return false
    if (search) {
      const q = search.toLowerCase()
      return s.title.toLowerCase().includes(q) || (s.preview ?? '').toLowerCase().includes(q)
    }
    return true
  })

  const grouped = groupByTime(filtered)

  return (
    <>
      {/* Mobile overlay backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/20 z-30 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar panel */}
      <div
        className="flex flex-col bg-white border-r border-slate-200 z-40 transition-all duration-200 shrink-0 overflow-hidden"
        style={{ width: isOpen ? '288px' : '0px' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-3 border-b border-slate-100">
          <h2 className="text-sm font-semibold text-slate-700">Chats</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={onNewChat}
              className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
              title="New chat"
            >
              <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors lg:hidden"
              title="Close"
            >
              <svg className="w-4 h-4 text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Search */}
        <div className="px-3 py-2">
          <input
            type="text"
            placeholder="Search chats..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full text-xs text-slate-700 placeholder-slate-400 bg-slate-50 border border-slate-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-300"
          />
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {loading && sessions.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">Loading...</p>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">
              {search ? 'No matching chats' : 'No conversations yet'}
            </p>
          ) : (
            GROUP_ORDER.map((label) => {
              const group = grouped[label]
              if (!group || group.length === 0) return null
              return (
                <div key={label} className="mb-2">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-3 py-1">
                    {label}
                  </p>
                  {group.map((s) => (
                    <SessionItem
                      key={s.session_id}
                      session={s}
                      isActive={s.session_id === currentSessionId}
                      onSelect={() => { onSelectSession(s.session_id); onClose() }}
                      onRename={(title) => handleRename(s.session_id, title)}
                      onArchive={() => handleArchive(s.session_id)}
                      onDelete={() => handleDelete(s.session_id)}
                    />
                  ))}
                </div>
              )
            })
          )}
        </div>

        {/* Footer: show archived toggle */}
        <div className="px-3 py-2 border-t border-slate-100">
          <label className="flex items-center gap-2 text-xs text-slate-500 cursor-pointer">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
              className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
            />
            Show archived
          </label>
        </div>
      </div>
    </>
  )
}
