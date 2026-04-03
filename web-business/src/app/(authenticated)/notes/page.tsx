'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'

interface Note {
  id?: string
  query?: string
  decision?: string
  created_at?: string
}

export default function NotesPage() {
  const [showEditor, setShowEditor] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [notes, setNotes] = useState<Note[]>([])
  const [loadingNotes, setLoadingNotes] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const fetchNotes = () => {
    setLoadingNotes(true)
    fetch('/api/notes/list')
      .then((r) => r.json())
      .then((data) => setNotes(Array.isArray(data) ? data : data.results ?? []))
      .catch(() => setNotes([]))
      .finally(() => setLoadingNotes(false))
  }

  useEffect(() => {
    fetchNotes()
  }, [])

  const handleSave = async () => {
    if (!content.trim()) return
    setSaving(true)
    setSaveError('')
    try {
      const res = await fetch('/api/notes/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim() || 'Untitled note', content }),
      })
      if (res.ok) {
        setTitle('')
        setContent('')
        setShowEditor(false)
        fetchNotes()
      } else {
        setSaveError('Failed to save. Please try again.')
      }
    } catch {
      setSaveError('Network error. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const noteTitle = (note: Note) =>
    note.query?.replace(/^Note:\s*/i, '') ?? 'Untitled'

  const notePreview = (note: Note) => {
    const text = note.decision ?? ''
    const firstLine = text.split('\n')[0]
    return firstLine.length > 100 ? firstLine.slice(0, 100) + '…' : firstLine
  }

  const noteDate = (note: Note) => {
    if (!note.created_at) return ''
    return new Date(note.created_at).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4 md:space-y-6">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">Your Notes</h2>
        {!showEditor && (
          <button
            onClick={() => {
              setShowEditor(true)
              setTitle('')
              setContent('')
              setSaveError('')
            }}
            className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors min-h-[44px]"
          >
            New Note
          </button>
        )}
      </div>

      {/* Editor */}
      {showEditor && (
        <div className="bg-white border border-slate-200 rounded-xl p-4 md:p-5 shadow-sm space-y-4">
          <input
            type="text"
            placeholder="Note title (optional)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full text-sm text-slate-800 placeholder-slate-400 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300"
          />
          <textarea
            placeholder="Write your note…"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full text-sm text-slate-800 placeholder-slate-400 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-y"
            style={{ minHeight: 200 }}
          />
          {saveError && <p className="text-sm text-red-600">{saveError}</p>}
          <div className="flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={saving || !content.trim()}
              className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => setShowEditor(false)}
              className="px-4 py-2.5 text-slate-600 hover:text-slate-900 text-sm font-medium transition-colors min-h-[44px]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Notes list */}
      {loadingNotes ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : notes.length === 0 ? (
        <p className="text-sm text-slate-400">No notes yet. Create your first note above.</p>
      ) : (
        <ul className="space-y-2">
          {notes.map((note, idx) => {
            const id = note.id ?? String(idx)
            const isExpanded = expandedId === id
            return (
              <li
                key={id}
                className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden"
              >
                <button
                  onClick={() => setExpandedId(isExpanded ? null : id)}
                  className="w-full text-left px-5 py-4 hover:bg-slate-50 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-800 truncate">
                        {noteTitle(note)}
                      </p>
                      {!isExpanded && (
                        <p className="text-xs text-slate-500 mt-0.5 truncate">
                          {notePreview(note)}
                        </p>
                      )}
                    </div>
                    <div className="flex-shrink-0 flex items-center gap-3">
                      <span className="text-xs text-slate-400">{noteDate(note)}</span>
                      <svg
                        className={`w-4 h-4 text-slate-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </div>
                  </div>
                </button>

                {isExpanded && (
                  <div className="px-5 pb-4 space-y-3 border-t border-slate-100 pt-3">
                    <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">
                      {note.decision}
                    </p>
                    <Link
                      href={`/ask?q=${encodeURIComponent(noteTitle(note))}`}
                      className="inline-flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 font-medium transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                      </svg>
                      Ask about this
                    </Link>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
