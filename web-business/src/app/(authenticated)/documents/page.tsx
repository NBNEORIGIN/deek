'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

interface DocumentEntry {
  id?: string
  query?: string
  decision?: string
  created_at?: string
}

interface UploadResult {
  success: boolean
  preview?: string
  error?: string
  filename?: string
}

const ACCEPTED_TYPES = '.pdf,.docx,.txt,.md,.csv'

export default function DocumentsPage() {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [documents, setDocuments] = useState<DocumentEntry[]>([])
  const [loadingDocs, setLoadingDocs] = useState(true)
  const [currentFile, setCurrentFile] = useState<string>('')

  const fileInputRef = useRef<HTMLInputElement>(null)

  // Fetch document list on mount
  useEffect(() => {
    fetch('/api/documents/list')
      .then((r) => r.json())
      .then((data) => setDocuments(Array.isArray(data) ? data : data.results ?? []))
      .catch(() => setDocuments([]))
      .finally(() => setLoadingDocs(false))
  }, [])

  const uploadFile = useCallback(async (file: File) => {
    setUploading(true)
    setUploadResult(null)
    setSaveStatus('idle')
    setCurrentFile(file.name)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch('/api/documents/upload', {
        method: 'POST',
        body: formData,
      })
      const data = await res.json()
      if (res.ok) {
        setUploadResult({ success: true, preview: data.preview, filename: file.name })
      } else {
        setUploadResult({ success: false, error: data.error ?? 'Upload failed' })
      }
    } catch {
      setUploadResult({ success: false, error: 'Network error. Please try again.' })
    } finally {
      setUploading(false)
    }
  }, [])

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) uploadFile(file)
    // Reset so same file can be re-uploaded
    e.target.value = ''
  }

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setDragging(false)
      const file = e.dataTransfer.files?.[0]
      if (file) uploadFile(file)
    },
    [uploadFile]
  )

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragging(true)
  }

  const handleDragLeave = () => setDragging(false)

  const saveToMemory = async () => {
    if (!uploadResult?.preview) return
    setSaveStatus('saving')
    try {
      const res = await fetch('/api/voice/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: uploadResult.preview,
          title: `Document: ${currentFile}`,
        }),
      })
      if (res.ok) {
        setSaveStatus('saved')
        // Refresh document list
        fetch('/api/documents/list')
          .then((r) => r.json())
          .then((data) => setDocuments(Array.isArray(data) ? data : data.results ?? []))
          .catch(() => null)
      } else {
        setSaveStatus('error')
      }
    } catch {
      setSaveStatus('error')
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 md:space-y-8">
      {/* Drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        className={
          'border-2 border-dashed rounded-xl p-6 md:p-10 flex flex-col items-center gap-4 transition-colors w-full ' +
          (dragging
            ? 'border-indigo-400 bg-indigo-50'
            : 'border-slate-300 bg-slate-50 hover:border-slate-400')
        }
      >
        <svg
          className={`w-12 h-12 ${dragging ? 'text-indigo-400' : 'text-slate-300'}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
        <div className="text-center">
          <p className="text-sm text-slate-600 font-medium">
            {dragging ? 'Drop to upload' : 'Drag and drop a file here'}
          </p>
          <p className="text-xs text-slate-400 mt-1">PDF, Word, text, Markdown, CSV</p>
        </div>
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="px-4 py-2.5 bg-white border border-slate-300 hover:border-indigo-400 text-slate-700 text-sm font-medium rounded-lg transition-colors disabled:opacity-50 min-h-[44px]"
        >
          Browse files
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_TYPES}
          onChange={handleFileChange}
          className="hidden"
        />
      </div>

      {/* Upload progress / result */}
      {uploading && (
        <div className="bg-white border border-slate-200 rounded-xl px-5 py-4 flex items-center gap-3 shadow-sm">
          <svg className="w-5 h-5 text-indigo-500 animate-spin flex-shrink-0" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <span className="text-sm text-slate-600">Uploading {currentFile}…</span>
        </div>
      )}

      {uploadResult && !uploading && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          {uploadResult.success ? (
            <>
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-green-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 00-1.414 0L8 12.586 4.707 9.293a1 1 0 00-1.414 1.414l4 4a1 1 0 001.414 0l8-8a1 1 0 000-1.414z" clipRule="evenodd" />
                </svg>
                <span className="text-sm font-medium text-slate-800">{currentFile} uploaded</span>
              </div>
              {uploadResult.preview && (
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Preview</p>
                  <p className="text-sm text-slate-700 bg-slate-50 rounded-lg p-3 font-mono whitespace-pre-wrap leading-relaxed">
                    {uploadResult.preview}
                  </p>
                </div>
              )}
              <div className="flex items-center gap-3">
                <button
                  onClick={saveToMemory}
                  disabled={saveStatus === 'saving' || saveStatus === 'saved'}
                  className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
                >
                  {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved' : 'Save to Memory'}
                </button>
                {saveStatus === 'saved' && (
                  <span className="text-sm text-green-600 font-medium">Saved successfully</span>
                )}
                {saveStatus === 'error' && (
                  <span className="text-sm text-red-600">Save failed — try again</span>
                )}
              </div>
            </>
          ) : (
            <div className="text-sm text-red-600">{uploadResult.error}</div>
          )}
        </div>
      )}

      {/* Previously uploaded documents */}
      <div>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Previously Saved</h2>
        {loadingDocs ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-slate-400">No documents saved yet.</p>
        ) : (
          <ul className="space-y-2">
            {documents.map((doc, idx) => (
              <li
                key={doc.id ?? idx}
                className="bg-white border border-slate-200 rounded-lg px-4 py-3 shadow-sm"
              >
                <p className="text-sm font-medium text-slate-800">
                  {doc.query ?? 'Untitled document'}
                </p>
                {doc.decision && (
                  <p className="text-xs text-slate-500 mt-1 line-clamp-2">
                    {doc.decision.slice(0, 120)}{doc.decision.length > 120 ? '…' : ''}
                  </p>
                )}
                {doc.created_at && (
                  <p className="text-xs text-slate-400 mt-1">
                    {new Date(doc.created_at).toLocaleDateString('en-GB', {
                      day: 'numeric',
                      month: 'short',
                      year: 'numeric',
                    })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
