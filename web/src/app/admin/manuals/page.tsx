'use client'

/**
 * /admin/manuals — drag-drop upload + ingestion of machinery manuals.
 *
 * Files land at /opt/nbne/manuals/<Machine>/<filename> on Hetzner via
 * the bind-mounted /app/data/manuals/ volume. The upload endpoint runs
 * the parsing + chunking + embedding pipeline inline, so by the time
 * the file finishes uploading it's already searchable via search_manuals
 * in the chat.
 *
 * ADMIN-only client-side; the server-side proxy enforces it too
 * (same pattern as /admin/users).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft, Loader2, Upload, AlertCircle, Check, Trash2,
  FileText, Image as ImageIcon, FileSpreadsheet,
  Wrench, Plus,
} from 'lucide-react'
import { BRAND } from '@/lib/brand'

interface ManualRow {
  machine: string
  file_path: string
  chunks: number
  last_indexed: string | null
}

const ACCEPT = '.pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.heic,.gif,.webp'
const MAX_BYTES = 50 * 1024 * 1024

export default function AdminManualsPage() {
  const [manuals, setManuals] = useState<ManualRow[]>([])
  const [machines, setMachines] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Upload form state
  const [machineSelected, setMachineSelected] = useState('')
  const [machineNew, setMachineNew] = useState('')
  const [staging, setStaging] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [recentResults, setRecentResults] = useState<Array<{
    name: string; ok: boolean; chunks?: number; embedded?: number; error?: string
  }>>([])
  const [dragActive, setDragActive] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [mRes, listRes] = await Promise.all([
        fetch('/api/admin/manuals/machines', { cache: 'no-store' }),
        fetch('/api/admin/manuals/list', { cache: 'no-store' }),
      ])
      if (mRes.status === 401 || listRes.status === 401) {
        window.location.href = '/voice/login?callbackUrl=/admin/manuals'
        return
      }
      if (mRes.status === 403 || listRes.status === 403) {
        window.location.href = '/voice'
        return
      }
      const md = await mRes.json().catch(() => ({}))
      const ld = await listRes.json().catch(() => ({}))
      setMachines(md.machines || [])
      setManuals(ld.manuals || [])
      if (!machineSelected && (md.machines || []).length > 0) {
        setMachineSelected(md.machines[0])
      }
    } catch (err: any) {
      setError(err?.message || 'Network error')
    } finally {
      setLoading(false)
    }
  // intentional: re-binding on machineSelected would loop the dropdown
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { reload() }, [reload])

  const stage = (incoming: FileList | File[] | null) => {
    if (!incoming) return
    const arr = Array.from(incoming).filter(f => {
      if (f.size > MAX_BYTES) {
        setRecentResults(prev => [
          { name: f.name, ok: false, error: `over 50 MB — too large` },
          ...prev,
        ])
        return false
      }
      return true
    })
    setStaging(prev => [...prev, ...arr])
  }

  const removeStaged = (i: number) =>
    setStaging(s => s.filter((_, idx) => idx !== i))

  const submit = async () => {
    const target = (machineNew.trim() || machineSelected || '').trim()
    if (!target) {
      setError('Pick a machine first (existing or new).')
      return
    }
    if (staging.length === 0) {
      setError('Drop a file or click the area to attach.')
      return
    }
    setError(null)
    setUploading(true)
    setUploadProgress({ done: 0, total: staging.length })
    setRecentResults([])

    for (let i = 0; i < staging.length; i++) {
      const f = staging[i]
      const fd = new FormData()
      fd.append('file', f, f.name)
      fd.append('machine', target)
      try {
        const res = await fetch('/api/admin/manuals/upload', {
          method: 'POST',
          body: fd,
        })
        if (res.status === 401) {
          window.location.href = '/voice/login?callbackUrl=/admin/manuals'
          return
        }
        const data = await res.json().catch(() => ({}))
        if (!res.ok) {
          setRecentResults(prev => [
            { name: f.name, ok: false, error: data?.detail || data?.error || `HTTP ${res.status}` },
            ...prev,
          ])
        } else {
          setRecentResults(prev => [
            { name: f.name, ok: true, chunks: data.chunks, embedded: data.embedded },
            ...prev,
          ])
        }
      } catch (err: any) {
        setRecentResults(prev => [
          { name: f.name, ok: false, error: err?.message || 'network error' },
          ...prev,
        ])
      }
      setUploadProgress({ done: i + 1, total: staging.length })
    }

    setUploading(false)
    setUploadProgress(null)
    setStaging([])
    setMachineNew('')
    if (fileRef.current) fileRef.current.value = ''
    await reload()
  }

  const onDelete = async (m: ManualRow) => {
    if (!confirm(`Delete "${m.file_path}"? Removes ${m.chunks} chunks + the file from disk.`)) return
    try {
      const res = await fetch(
        `/api/admin/manuals/by-path?file_path=${encodeURIComponent(m.file_path)}`,
        { method: 'DELETE' },
      )
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data?.detail || data?.error || `HTTP ${res.status}`)
        return
      }
      await reload()
    } catch (err: any) {
      setError(err?.message || 'delete failed')
    }
  }

  // Drag-drop on the dropzone (window-level would be too aggressive
  // here — admin page may have other things you want to drag).
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(true)
  }
  const onDragLeave = () => setDragActive(false)
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragActive(false)
    if (e.dataTransfer?.files) stage(e.dataTransfer.files)
  }

  return (
    <div className="min-h-[100dvh] bg-white text-gray-900">
      <header className="flex items-center justify-between border-b border-gray-200 px-4 py-2">
        <div className="flex items-center gap-3">
          <Link
            href="/voice"
            className="rounded-md p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
            title="Back to chat"
          >
            <ArrowLeft size={16} />
          </Link>
          <div className="flex items-center gap-2 text-sm font-semibold tracking-tight">
            <Wrench size={14} />
            Machinery manuals
          </div>
        </div>
        <div className="text-xs text-gray-500">{BRAND}</div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        {/* ── Upload form ───────────────────────────────────────── */}
        <section className="mb-8">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
            Upload a manual
          </h2>

          <div className="mb-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs text-gray-500">
                Existing machine
              </label>
              <select
                value={machineSelected}
                onChange={e => { setMachineSelected(e.target.value); setMachineNew('') }}
                disabled={uploading}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400 disabled:opacity-60"
              >
                {machines.length === 0 && (
                  <option value="">— no machines yet —</option>
                )}
                {machines.map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-500">
                Or new machine name
              </label>
              <div className="flex items-center gap-1">
                <Plus size={14} className="text-gray-400" />
                <input
                  type="text"
                  value={machineNew}
                  onChange={e => setMachineNew(e.target.value)}
                  disabled={uploading}
                  placeholder="e.g. Hulk, Beast, Rolf, Mao"
                  className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm placeholder-gray-400 focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400 disabled:opacity-60"
                />
              </div>
              <div className="mt-1 text-[10px] text-gray-400">
                Takes precedence over the dropdown when set.
              </div>
            </div>
          </div>

          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={
              'flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-sm text-gray-500 transition cursor-pointer ' +
              (dragActive
                ? 'border-emerald-400 bg-emerald-50 text-emerald-700'
                : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50')
            }
          >
            <Upload size={20} />
            <div className="font-medium text-gray-700">
              {dragActive ? 'Drop to attach' : 'Drag files here or click to browse'}
            </div>
            <div className="text-[11px] text-gray-400">
              PDF · DOCX · TXT · PNG · JPG · HEIC — up to 50 MB each
            </div>
          </div>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={e => stage(e.target.files)}
          />

          {staging.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {staging.map((f, i) => (
                <div
                  key={i}
                  className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-gray-50 px-2.5 py-1 text-xs"
                >
                  {iconForFile(f.name)}
                  <span className="font-mono text-gray-700">{f.name}</span>
                  <span className="text-gray-400">({(f.size / 1024 / 1024).toFixed(1)} MB)</span>
                  {!uploading && (
                    <button
                      onClick={() => removeStaged(i)}
                      className="text-gray-400 hover:text-rose-600"
                      title="Remove"
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="mt-3 flex items-center justify-between">
            <div className="text-xs text-gray-500">
              {uploadProgress
                ? `Uploading ${uploadProgress.done} / ${uploadProgress.total}…`
                : staging.length > 0
                  ? `${staging.length} file${staging.length === 1 ? '' : 's'} ready`
                  : ''}
            </div>
            <button
              onClick={submit}
              disabled={uploading || staging.length === 0}
              className="inline-flex items-center gap-2 rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-40"
            >
              {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              {uploading ? 'Ingesting…' : 'Upload + ingest'}
            </button>
          </div>

          {recentResults.length > 0 && (
            <div className="mt-3 space-y-1">
              {recentResults.map((r, i) => (
                <div
                  key={i}
                  className={
                    'flex items-center gap-2 rounded-md px-2.5 py-1 text-xs ' +
                    (r.ok
                      ? 'bg-emerald-50 text-emerald-800'
                      : 'bg-rose-50 text-rose-700')
                  }
                >
                  {r.ok ? <Check size={12} /> : <AlertCircle size={12} />}
                  <span className="font-mono">{r.name}</span>
                  {r.ok ? (
                    <span>— {r.chunks} chunks ({r.embedded} embedded)</span>
                  ) : (
                    <span>— {r.error}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ── List of ingested manuals ──────────────────────────── */}
        <section>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-gray-500">
            Ingested manuals
          </h2>

          {loading && (
            <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
              <Loader2 size={14} className="animate-spin" />
              Loading…
            </div>
          )}

          {!loading && manuals.length === 0 && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-6 text-center text-sm text-gray-500">
              No manuals ingested yet. Upload one above to get started.
            </div>
          )}

          {manuals.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-gray-200">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wider text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Machine</th>
                    <th className="px-3 py-2 text-left font-medium">File</th>
                    <th className="px-3 py-2 text-right font-medium">Chunks</th>
                    <th className="px-3 py-2 text-left font-medium">Indexed</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {manuals.map(m => (
                    <tr
                      key={m.file_path}
                      className="border-t border-gray-100 hover:bg-gray-50"
                    >
                      <td className="px-3 py-2 font-medium text-gray-700">
                        {m.machine}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-700">
                        {m.file_path}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-700">
                        {m.chunks}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {m.last_indexed
                          ? new Date(m.last_indexed).toLocaleString('en-GB')
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          onClick={() => onDelete(m)}
                          className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 hover:bg-rose-50 hover:text-rose-700"
                          title="Delete (removes chunks + file)"
                        >
                          <Trash2 size={11} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}

function iconForFile(name: string) {
  const ext = name.toLowerCase().split('.').pop() || ''
  if (['png', 'jpg', 'jpeg', 'heic', 'gif', 'webp', 'bmp'].includes(ext)) {
    return <ImageIcon size={12} className="text-gray-500" />
  }
  if (['xlsx', 'xlsm', 'csv', 'tsv'].includes(ext)) {
    return <FileSpreadsheet size={12} className="text-gray-500" />
  }
  return <FileText size={12} className="text-gray-500" />
}
