'use client'

import { useState, useRef, useEffect } from 'react'
import type { SessionSummary } from '@/types/chat'

// Shared with ask/page.tsx — strips context-injection blocks that the chat
// stream route prepends to user messages ([PERSONALITY], [LIVE BUSINESS DATA],
// [WIKI CONTEXT], [CRM DATA], [IMPORTANT:…], [FILE UPLOADED:…]). Handles the
// case where the block is truncated so the closing tag is missing.
const INJECTION_PATTERNS: RegExp[] = [
  /\[PERSONALITY\][\s\S]*?\[END PERSONALITY\]\s*/gi,
  /\[LIVE BUSINESS DATA[\s\S]*?\[END LIVE DATA\]\s*/gi,
  /\[WIKI CONTEXT[\s\S]*?\[END WIKI CONTEXT\]\s*/gi,
  /\[CRM DATA[\s\S]*?\[END CRM DATA\]\s*/gi,
  /\[IMPORTANT:[\s\S]*?\]\s*/gi,
  /\[FILE UPLOADED:[^\]]*\]\s*/gi,
  /\[FILE UPLOAD FAILED:[^\]]*\]\s*/gi,
]

const TRUNCATED_BLOCK_PREFIX =
  /^\s*\[(PERSONALITY|LIVE BUSINESS DATA|WIKI CONTEXT|CRM DATA|IMPORTANT)/i

export function stripInjectionBlocks(raw: string): string {
  let cleaned = raw ?? ''
  for (const pat of INJECTION_PATTERNS) {
    cleaned = cleaned.replace(pat, '')
  }
  cleaned = cleaned.replace(/^\s+/, '')
  // If a truncated block is still clinging to the front, drop the first
  // line. One more check in case two blocks were both truncated.
  if (TRUNCATED_BLOCK_PREFIX.test(cleaned)) {
    const nl = cleaned.indexOf('\n')
    if (nl >= 0) {
      cleaned = cleaned.slice(nl + 1).replace(/^\s+/, '')
      if (TRUNCATED_BLOCK_PREFIX.test(cleaned)) return ''
    } else {
      return ''
    }
  }
  return cleaned
}

export function cleanTitle(raw: string): string {
  const stripped = stripInjectionBlocks(raw).trim()
  if (!stripped) return 'New conversation'
  const firstLine = stripped.split('\n')[0] ?? ''
  return firstLine.slice(0, 40) || 'New conversation'
}

function timeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

interface SessionItemProps {
  session: SessionSummary
  isActive: boolean
  onSelect: () => void
  onRename: (title: string) => void
  onArchive: () => void
  onDelete: () => void
}

export default function SessionItem({
  session,
  isActive,
  onSelect,
  onRename,
  onArchive,
  onDelete,
}: SessionItemProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(session.title)
  const inputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editing])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    if (menuOpen) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [menuOpen])

  function handleRenameSubmit() {
    const trimmed = editTitle.trim()
    if (trimmed && trimmed !== session.title) {
      onRename(trimmed)
    }
    setEditing(false)
  }

  return (
    <div
      className={
        'group relative flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ' +
        (isActive
          ? 'bg-indigo-50 border border-indigo-200'
          : 'hover:bg-slate-50 border border-transparent')
      }
      onClick={() => !editing && onSelect()}
    >
      <div className="flex-1 min-w-0">
        {editing ? (
          <input
            ref={inputRef}
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={handleRenameSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRenameSubmit()
              if (e.key === 'Escape') { setEditing(false); setEditTitle(session.title) }
            }}
            className="w-full text-sm text-slate-800 bg-white border border-indigo-300 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <>
            <p className="text-sm text-slate-800 truncate font-medium leading-tight">
              {cleanTitle(session.title)}
            </p>
            {(() => {
              const previewText = stripInjectionBlocks(session.preview ?? '')
                .replace(/\s+/g, ' ')
                .trim()
              if (previewText) {
                return (
                  <p className="text-xs text-slate-500 truncate mt-0.5">
                    {previewText}
                  </p>
                )
              }
              return null
            })()}
            <p className="text-[10px] text-slate-400 truncate mt-0.5">
              {timeAgo(session.last_message_at)}
            </p>
          </>
        )}
      </div>

      {/* Three-dot menu */}
      {!editing && (
        <div ref={menuRef} className="relative flex-shrink-0">
          <button
            onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen) }}
            className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded hover:bg-slate-200 transition-opacity"
            aria-label="Session options"
          >
            <svg className="w-4 h-4 text-slate-400" fill="currentColor" viewBox="0 0 20 20">
              <path d="M10 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4z" />
            </svg>
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-7 z-50 w-36 bg-white border border-slate-200 rounded-lg shadow-lg py-1">
              <button
                className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                onClick={(e) => {
                  e.stopPropagation()
                  setMenuOpen(false)
                  setEditTitle(session.title)
                  setEditing(true)
                }}
              >
                Rename
              </button>
              {!session.archived && (
                <button
                  className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                  onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onArchive() }}
                >
                  Archive
                </button>
              )}
              <button
                className="w-full text-left px-3 py-1.5 text-xs text-red-600 hover:bg-red-50"
                onClick={(e) => {
                  e.stopPropagation()
                  setMenuOpen(false)
                  if (confirm('Delete this conversation permanently?')) onDelete()
                }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      )}

      {/* Archive badge */}
      {session.archived && (
        <span className="flex-shrink-0 text-[10px] text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">
          archived
        </span>
      )}
    </div>
  )
}
