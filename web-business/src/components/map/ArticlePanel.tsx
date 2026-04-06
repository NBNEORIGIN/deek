'use client'

import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'

interface GraphNode {
  id: string
  label: string
  description: string
  status: string
  article_path: string | null
  category: string
}

interface ArticlePanelProps {
  node: GraphNode | null
  onClose: () => void
}

export default function ArticlePanel({ node, onClose }: ArticlePanelProps) {
  const [article, setArticle] = useState<string>('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!node?.article_path) {
      setArticle('')
      return
    }

    setLoading(true)
    const path = node.article_path.replace(/^wiki\//, '').replace(/\.md$/, '')
    fetch(`/api/wiki/article/${path}`)
      .then((res) => {
        if (!res.ok) throw new Error('Article not found')
        return res.json()
      })
      .then((data) => setArticle(data.content ?? ''))
      .catch(() => setArticle('Article not available.'))
      .finally(() => setLoading(false))
  }, [node])

  return (
    <div
      className={
        'absolute top-0 right-0 h-full bg-slate-900 border-l border-slate-700 ' +
        'transition-transform duration-300 ease-in-out overflow-y-auto z-20 ' +
        (node ? 'translate-x-0' : 'translate-x-full')
      }
      style={{ width: 420, maxWidth: '100%' }}
    >
      {node && (
        <div className="p-6">
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div>
              <h2 className="text-lg font-bold text-slate-100">{node.label}</h2>
              <p className="text-sm text-slate-400 mt-0.5">{node.description}</p>
              <span
                className={
                  'inline-block mt-1.5 text-xs font-medium px-2 py-0.5 rounded capitalize ' +
                  (node.status === 'production'
                    ? 'bg-green-900/50 text-green-400'
                    : node.status === 'development'
                    ? 'bg-amber-900/50 text-amber-400'
                    : 'bg-slate-800 text-slate-400')
                }
              >
                {node.status}
              </span>
            </div>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-200 p-1"
              aria-label="Close panel"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Article content */}
          {loading ? (
            <div className="text-slate-400 text-sm py-8 text-center">Loading article...</div>
          ) : article ? (
            <div className="prose prose-invert prose-sm max-w-none prose-headings:text-slate-200 prose-p:text-slate-300 prose-li:text-slate-300 prose-strong:text-slate-200 prose-a:text-indigo-400">
              <ReactMarkdown>{article}</ReactMarkdown>
            </div>
          ) : node.status === 'planned' ? (
            <div className="text-slate-500 text-sm py-8 text-center">
              This module is planned. No article available yet.
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
