'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

interface MemoryEntry {
  id: string
  query?: string
  description?: string
  decision?: string
  rejected?: string
  model?: string
  entry_type?: string
  created_at?: string
  updated_at?: string
}

interface ListResponse {
  results: MemoryEntry[]
  total: number
}

const PAGE_SIZE = 50

function formatDate(iso?: string) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function preview(text?: string) {
  if (!text) return ''
  return text.length > 100 ? text.slice(0, 100) + '…' : text
}

function typeLabel(entry: MemoryEntry) {
  const t = entry.entry_type
  if (!t) return '—'
  return t.charAt(0).toUpperCase() + t.slice(1).replace(/_/g, ' ')
}

// ── Inline editor ──────────────────────────────────────────────────────────

interface EditFormProps {
  entry: MemoryEntry
  onSave: (updated: Partial<MemoryEntry>) => Promise<void>
  onCancel: () => void
}

function EditForm({ entry, onSave, onCancel }: EditFormProps) {
  const [description, setDescription] = useState(entry.description ?? entry.decision ?? '')
  const [query, setQuery] = useState(entry.query ?? '')
  const [rejected, setRejected] = useState(entry.rejected ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSave() {
    setSaving(true)
    setError('')
    try {
      await onSave({ description, query, rejected })
    } catch {
      setError('Save failed — please try again.')
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4 pt-2">
      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
          Topic
        </label>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          placeholder="What this memory is about"
        />
      </div>
      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
          Content
        </label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={6}
          className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-y"
          placeholder="What was decided or recorded"
        />
      </div>
      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
          Alternatives considered
        </label>
        <textarea
          value={rejected}
          onChange={(e) => setRejected(e.target.value)}
          rows={3}
          className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-y"
          placeholder="What was ruled out, and why"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 min-h-[44px]"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2.5 border border-slate-300 hover:border-slate-400 text-slate-700 text-sm font-medium rounded-lg transition-colors min-h-[44px]"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Expanded row ────────────────────────────────────────────────────────────

interface ExpandedRowProps {
  entry: MemoryEntry
  onClose: () => void
  onDeleted: (id: string) => void
  onUpdated: (entry: MemoryEntry) => void
}

function ExpandedRow({ entry, onClose, onDeleted, onUpdated }: ExpandedRowProps) {
  const [mode, setMode] = useState<'view' | 'edit' | 'confirm-delete'>('view')
  const [deleting, setDeleting] = useState(false)

  const content = entry.description ?? entry.decision ?? ''

  async function handleSave(updated: Partial<MemoryEntry>) {
    const res = await fetch(`/api/memory/entries/${entry.id}?project=nbne`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updated),
    })
    if (!res.ok) throw new Error('Save failed')
    const data: MemoryEntry = await res.json()
    onUpdated({ ...entry, ...data, ...updated })
    setMode('view')
  }

  async function handleDelete() {
    setDeleting(true)
    try {
      const res = await fetch(`/api/memory/entries/${entry.id}?project=nbne`, {
        method: 'DELETE',
      })
      if (res.ok || res.status === 204) {
        onDeleted(entry.id)
      }
    } finally {
      setDeleting(false)
    }
  }

  if (mode === 'edit') {
    return (
      <EditForm
        entry={entry}
        onSave={handleSave}
        onCancel={() => setMode('view')}
      />
    )
  }

  return (
    <div className="space-y-4 pt-2">
      {/* Main content */}
      {content && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Content</p>
          <div className="bg-slate-50 rounded-lg p-3 max-h-60 overflow-y-auto">
            <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">{content}</p>
          </div>
        </div>
      )}

      {/* Meta fields */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {entry.query && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Topic</p>
            <p className="text-sm text-slate-700">{entry.query}</p>
          </div>
        )}
        {entry.rejected && (
          <div className="sm:col-span-2">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Alternatives considered</p>
            <p className="text-sm text-slate-600 italic">{entry.rejected}</p>
          </div>
        )}
        {entry.model && (
          <div>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Model</p>
            <p className="text-sm text-slate-600">{entry.model}</p>
          </div>
        )}
      </div>

      {/* Confirm delete prompt */}
      {mode === 'confirm-delete' && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex flex-col sm:flex-row sm:items-center gap-3">
          <p className="text-sm text-red-700 font-medium flex-1">
            Are you sure? This cannot be undone.
          </p>
          <div className="flex gap-3">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2.5 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 min-h-[44px]"
            >
              {deleting ? 'Deleting…' : 'Yes, delete'}
            </button>
            <button
              onClick={() => setMode('view')}
              className="px-4 py-2.5 border border-slate-300 hover:border-slate-400 text-slate-700 text-sm font-medium rounded-lg transition-colors min-h-[44px]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Action buttons */}
      {mode === 'view' && (
        <div className="flex flex-wrap gap-3 pt-1">
          <button
            onClick={() => setMode('edit')}
            className="px-4 py-2.5 border border-slate-300 hover:border-indigo-400 text-slate-700 hover:text-indigo-700 text-sm font-medium rounded-lg transition-colors min-h-[44px]"
          >
            Edit
          </button>
          <button
            onClick={() => setMode('confirm-delete')}
            className="px-4 py-2.5 border border-red-200 hover:border-red-400 text-red-600 hover:text-red-700 text-sm font-medium rounded-lg transition-colors min-h-[44px]"
          >
            Delete
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2.5 border border-slate-300 hover:border-slate-400 text-slate-600 text-sm font-medium rounded-lg transition-colors min-h-[44px]"
          >
            Close
          </button>
        </div>
      )}
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Debounce search input
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(search)
      setOffset(0)
    }, 500)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [search])

  // Fetch entries
  const fetchEntries = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams({
      project: 'nbne',
      limit: String(PAGE_SIZE),
      offset: String(offset),
    })
    if (debouncedSearch) params.set('q', debouncedSearch)

    try {
      const res = await fetch(`/api/memory/entries?${params.toString()}`)
      if (!res.ok) {
        setEntries([])
        setTotal(0)
        return
      }
      const data: ListResponse = await res.json()
      const results = Array.isArray(data) ? data : (data.results ?? [])
      setEntries(results)
      setTotal(Array.isArray(data) ? results.length : (data.total ?? results.length))
    } catch {
      setEntries([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [debouncedSearch, offset])

  useEffect(() => {
    fetchEntries()
  }, [fetchEntries])

  function handleDeleted(id: string) {
    setEntries((prev) => prev.filter((e) => e.id !== id))
    setTotal((prev) => Math.max(0, prev - 1))
    setExpandedId(null)
  }

  function handleUpdated(updated: MemoryEntry) {
    setEntries((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
  }

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Search bar */}
      <div className="relative">
        <svg
          className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z"
          />
        </svg>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search memory…"
          className="w-full pl-10 pr-4 py-3 border border-slate-300 rounded-xl text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white min-h-[44px]"
        />
      </div>

      {/* Table */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden">
        {/* Desktop table header */}
        <div className="hidden sm:grid grid-cols-[160px_1fr_200px_100px] gap-4 px-4 py-3 bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-500 uppercase tracking-wide">
          <span>Date</span>
          <span>Topic</span>
          <span>Preview</span>
          <span>Type</span>
        </div>

        {loading ? (
          <div className="px-4 py-10 text-center text-sm text-slate-400">Loading…</div>
        ) : entries.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-slate-400">
            {debouncedSearch ? 'No results for that search.' : 'No memory entries yet.'}
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {entries.map((entry) => {
              const isExpanded = expandedId === entry.id
              const content = entry.description ?? entry.decision ?? ''

              return (
                <li key={entry.id}>
                  {/* Row — clickable to expand */}
                  <button
                    onClick={() => toggleExpand(entry.id)}
                    className="w-full text-left px-4 py-3 hover:bg-slate-50 transition-colors min-h-[52px] focus:outline-none focus:bg-slate-50"
                    aria-expanded={isExpanded}
                  >
                    {/* Mobile: stacked layout */}
                    <div className="sm:hidden space-y-0.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-slate-800 truncate">
                          {entry.query ?? 'Untitled'}
                        </span>
                        <span className="text-xs text-slate-400 whitespace-nowrap shrink-0">
                          {formatDate(entry.created_at)}
                        </span>
                      </div>
                      <p className="text-xs text-slate-500 line-clamp-2">{preview(content)}</p>
                    </div>

                    {/* Desktop: grid layout */}
                    <div className="hidden sm:grid grid-cols-[160px_1fr_200px_100px] gap-4 items-start">
                      <span className="text-sm text-slate-500 pt-0.5">{formatDate(entry.created_at)}</span>
                      <span className="text-sm font-medium text-slate-800 truncate">
                        {entry.query ?? 'Untitled'}
                      </span>
                      <span className="text-sm text-slate-500 line-clamp-2">{preview(content)}</span>
                      <span className="text-xs text-slate-400">{typeLabel(entry)}</span>
                    </div>
                  </button>

                  {/* Expanded content */}
                  {isExpanded && (
                    <div className="px-4 pb-5 border-t border-slate-100 bg-slate-50/60">
                      <ExpandedRow
                        entry={entry}
                        onClose={() => setExpandedId(null)}
                        onDeleted={handleDeleted}
                        onUpdated={handleUpdated}
                      />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <span className="text-sm text-slate-500">
            {from}–{to} of {total}
          </span>
          <div className="flex gap-3">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="px-4 py-2.5 border border-slate-300 hover:border-slate-400 text-slate-700 text-sm font-medium rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed min-h-[44px]"
            >
              Previous
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={to >= total}
              className="px-4 py-2.5 border border-slate-300 hover:border-slate-400 text-slate-700 text-sm font-medium rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed min-h-[44px]"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
