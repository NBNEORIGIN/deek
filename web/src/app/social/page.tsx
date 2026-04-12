'use client'

/**
 * Cairn Social — Phase 1 UI
 *
 * Two-panel layout (centre + right) per CAIRN_SOCIAL_V2_HANDOFF.md Blocker 4.
 * Three platforms: Facebook, Instagram, LinkedIn (no TikTok per Blocker 3).
 * Two input modes:
 *   - Brief    : Jo gives a short prompt → tool drafts in her voice
 *   - Proofread: Jo writes a finished post → tool refines/adapts per platform
 *
 * Calls proxied through /api/social/[...path] → backend /social/*
 * so they work in Docker where the browser can't reach cairn-api:8765.
 */

import { useEffect, useState } from 'react'

// Proxied through Next.js API route → backend /social/*
const API_BASE = '/api'

type Platform = 'facebook' | 'instagram' | 'linkedin'
type Pillar = 'job' | 'what_we_do' | 'team' | 'development'
type Mode = 'brief' | 'proofread'
type Tab = 'compose' | 'published' | 'voice'

const ALL_PLATFORMS: Platform[] = ['facebook', 'instagram', 'linkedin']
const PILLAR_LABELS: Record<Pillar, string> = {
  job: 'Job',
  what_we_do: 'What we do',
  team: 'Team',
  development: 'Development',
}
const PLATFORM_LABELS: Record<Platform, string> = {
  facebook: 'Facebook',
  instagram: 'Instagram',
  linkedin: 'LinkedIn',
}

interface Variant {
  id: number
  draft_id: number
  platform: Platform
  content: string
  generated_at: string
  generation_model: string
  revision_count: number
  is_published: boolean
  published_at: string | null
  published_url: string | null
}

interface DraftResponse {
  draft_id: number
  detected_pillar: Pillar | null
  variants: Variant[]
  notes_for_jo: string
  usage: { model: string; input_tokens: number; output_tokens: number; cost_gbp: number }
}

interface PublishedRow extends Variant {
  source_mode: Mode
  brief_text: string | null
  original_text: string | null
  content_pillar: Pillar | null
}

// ── small UI helpers ──────────────────────────────────────────────────────────

function classNames(...xs: (string | false | null | undefined)[]) {
  return xs.filter(Boolean).join(' ')
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        } catch {
          /* ignore */
        }
      }}
      className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function SocialPage() {
  const [tab, setTab] = useState<Tab>('compose')
  const [mode, setMode] = useState<Mode>('brief')
  const [brief, setBrief] = useState('')
  const [originalText, setOriginalText] = useState('')
  const [pillar, setPillar] = useState<Pillar | ''>('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([...ALL_PLATFORMS])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<DraftResponse | null>(null)
  const [variants, setVariants] = useState<Variant[]>([])

  const [refineText, setRefineText] = useState<Record<number, string>>({})
  const [refining, setRefining] = useState<number | null>(null)
  const [publishing, setPublishing] = useState<number | null>(null)

  const [published, setPublished] = useState<PublishedRow[]>([])
  const [publishedLoading, setPublishedLoading] = useState(false)

  const [voiceGuide, setVoiceGuide] = useState<{
    voice_guide: string
    seed_posts: { title: string; pillar: string; platform: string; content: string }[]
    version: number
  } | null>(null)

  const togglePlatform = (p: Platform) => {
    setSelectedPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    )
  }

  const handleGenerate = async () => {
    setError(null)
    setGenerating(true)
    setResponse(null)
    setVariants([])
    try {
      const url =
        mode === 'brief'
          ? `${API_BASE}/social/drafts`
          : `${API_BASE}/social/drafts/proofread`
      const body =
        mode === 'brief'
          ? { brief, platforms: selectedPlatforms, content_pillar: pillar || null }
          : {
              original_text: originalText,
              platforms: selectedPlatforms,
              content_pillar: pillar || null,
            }
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const detail = await r.text()
        throw new Error(`${r.status}: ${detail}`)
      }
      const data = (await r.json()) as DraftResponse
      setResponse(data)
      setVariants(data.variants)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setGenerating(false)
    }
  }

  const handleRefine = async (variantId: number) => {
    const instruction = (refineText[variantId] || '').trim()
    if (!instruction) return
    setRefining(variantId)
    setError(null)
    try {
      const r = await fetch(`${API_BASE}/social/variants/${variantId}/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction }),
      })
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
      const data = await r.json()
      const newVariant: Variant = data.variant
      // Replace the variant in-place by platform (refinement supersedes)
      setVariants((prev) =>
        prev.map((v) => (v.platform === newVariant.platform ? newVariant : v)),
      )
      setRefineText((prev) => ({ ...prev, [variantId]: '' }))
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setRefining(null)
    }
  }

  const handleRegenerate = async (variantId: number) => {
    setRefining(variantId)
    setError(null)
    try {
      const r = await fetch(`${API_BASE}/social/variants/${variantId}/regenerate`, {
        method: 'POST',
      })
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
      const data = await r.json()
      const newVariant: Variant = data.variant
      setVariants((prev) =>
        prev.map((v) => (v.platform === newVariant.platform ? newVariant : v)),
      )
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setRefining(null)
    }
  }

  const handlePublish = async (variantId: number) => {
    setPublishing(variantId)
    setError(null)
    try {
      const r = await fetch(`${API_BASE}/social/variants/${variantId}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
      const data = await r.json()
      const updated: Variant = data.variant
      setVariants((prev) =>
        prev.map((v) => (v.id === updated.id ? updated : v)),
      )
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setPublishing(null)
    }
  }

  const loadPublished = async () => {
    setPublishedLoading(true)
    try {
      const r = await fetch(`${API_BASE}/social/published?limit=50`)
      if (r.ok) {
        const data = await r.json()
        setPublished(data.published || [])
      }
    } finally {
      setPublishedLoading(false)
    }
  }

  const loadVoiceGuide = async () => {
    const r = await fetch(`${API_BASE}/social/voice-guide`)
    if (r.ok) {
      const data = await r.json()
      setVoiceGuide(data)
    }
  }

  useEffect(() => {
    if (tab === 'published') loadPublished()
    if (tab === 'voice' && !voiceGuide) loadVoiceGuide()
  }, [tab])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── render ──────────────────────────────────────────────────────────────────

  return (
    <main className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="mx-auto max-w-[1400px]">
        <header className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Cairn Social</h1>
            <p className="mt-1 text-sm text-slate-600">
              Drafting + proof-reading for Jo. Three platforms, no direct posting,
              copy-to-clipboard only.
            </p>
          </div>
          <nav className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1">
            {(['compose', 'published', 'voice'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={classNames(
                  'rounded-md px-3 py-1.5 text-sm font-medium',
                  tab === t
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-600 hover:bg-slate-100',
                )}
              >
                {t === 'compose' ? 'New post' : t === 'published' ? 'Published' : 'Voice guide'}
              </button>
            ))}
          </nav>
        </header>

        {error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {tab === 'compose' && (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            {/* ─── CENTRE: input ─────────────────────────────────────────── */}
            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-4 flex gap-1 rounded-md border border-slate-200 bg-slate-50 p-1">
                <button
                  onClick={() => setMode('brief')}
                  className={classNames(
                    'flex-1 rounded px-3 py-1.5 text-sm font-medium',
                    mode === 'brief' ? 'bg-white text-slate-900 shadow' : 'text-slate-600',
                  )}
                >
                  Draft from a brief
                </button>
                <button
                  onClick={() => setMode('proofread')}
                  className={classNames(
                    'flex-1 rounded px-3 py-1.5 text-sm font-medium',
                    mode === 'proofread' ? 'bg-white text-slate-900 shadow' : 'text-slate-600',
                  )}
                >
                  Proof-read my post
                </button>
              </div>

              {mode === 'brief' ? (
                <>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
                    What do you want to post about?
                  </label>
                  <textarea
                    value={brief}
                    onChange={(e) => setBrief(e.target.value)}
                    rows={8}
                    placeholder="Drop a photo, paste a customer message, or just tell me what happened — names, places, and concrete details are gold."
                    className="w-full resize-y rounded-md border border-slate-300 p-3 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                  />
                </>
              ) : (
                <>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
                    Paste your finished post
                  </label>
                  <textarea
                    value={originalText}
                    onChange={(e) => setOriginalText(e.target.value)}
                    rows={10}
                    placeholder="Write your post the way you'd post it. I'll fix typos, smooth phrasing, and adapt it for each platform — without rewriting it."
                    className="w-full resize-y rounded-md border border-slate-300 p-3 text-sm text-slate-900 focus:border-slate-500 focus:outline-none"
                  />
                </>
              )}

              <div className="mt-4">
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
                  Content pillar (optional — auto-detected if left blank)
                </label>
                <div className="flex flex-wrap gap-2">
                  {(Object.keys(PILLAR_LABELS) as Pillar[]).map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setPillar(pillar === p ? '' : p)}
                      className={classNames(
                        'rounded-full border px-3 py-1 text-xs font-medium',
                        pillar === p
                          ? 'border-slate-900 bg-slate-900 text-white'
                          : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50',
                      )}
                    >
                      {PILLAR_LABELS[p]}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-4">
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">
                  Platforms
                </label>
                <div className="flex flex-wrap gap-2">
                  {ALL_PLATFORMS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => togglePlatform(p)}
                      className={classNames(
                        'rounded-md border px-3 py-1.5 text-xs font-medium',
                        selectedPlatforms.includes(p)
                          ? 'border-slate-900 bg-slate-900 text-white'
                          : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50',
                      )}
                    >
                      {PLATFORM_LABELS[p]}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                onClick={handleGenerate}
                disabled={
                  generating ||
                  selectedPlatforms.length === 0 ||
                  (mode === 'brief' ? !brief.trim() : !originalText.trim())
                }
                className="mt-5 w-full rounded-md bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
              >
                {generating
                  ? 'Working…'
                  : mode === 'brief'
                    ? 'Draft posts'
                    : 'Proof-read my post'}
              </button>

              {response?.notes_for_jo && (
                <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                  <strong>Notes:</strong> {response.notes_for_jo}
                </div>
              )}

              {response && (
                <div className="mt-3 text-xs text-slate-500">
                  Model {response.usage.model} · {response.usage.input_tokens}↓ /{' '}
                  {response.usage.output_tokens}↑ tokens · ≈£
                  {response.usage.cost_gbp.toFixed(4)}
                </div>
              )}
            </section>

            {/* ─── RIGHT: drafts output ──────────────────────────────────── */}
            <section className="space-y-4">
              {variants.length === 0 && !generating && (
                <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-500">
                  Drafts will appear here once you click {mode === 'brief' ? '"Draft posts"' : '"Proof-read my post"'}.
                </div>
              )}
              {variants.map((v) => (
                <div
                  key={v.id}
                  className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-slate-900 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-white">
                        {PLATFORM_LABELS[v.platform]}
                      </span>
                      {v.is_published && (
                        <span className="rounded bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                          Published
                        </span>
                      )}
                      {v.revision_count > 0 && (
                        <span className="text-xs text-slate-500">
                          rev {v.revision_count}
                        </span>
                      )}
                    </div>
                    <CopyButton text={v.content} />
                  </div>
                  <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-800">
                    {v.content}
                  </pre>

                  <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
                    <div className="flex gap-2">
                      <input
                        value={refineText[v.id] || ''}
                        onChange={(e) =>
                          setRefineText((prev) => ({ ...prev, [v.id]: e.target.value }))
                        }
                        placeholder='Refine: "make it shorter", "mention Kevin"…'
                        className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-xs text-slate-900 focus:border-slate-500 focus:outline-none"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleRefine(v.id)
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => handleRefine(v.id)}
                        disabled={refining === v.id || !(refineText[v.id] || '').trim()}
                        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                      >
                        {refining === v.id ? '…' : 'Refine'}
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleRegenerate(v.id)}
                        disabled={refining === v.id}
                        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                      >
                        Regenerate
                      </button>
                      {!v.is_published && (
                        <button
                          type="button"
                          onClick={() => handlePublish(v.id)}
                          disabled={publishing === v.id}
                          className="rounded-md bg-green-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-700 disabled:opacity-50"
                        >
                          {publishing === v.id ? 'Publishing…' : 'Mark as published'}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </section>
          </div>
        )}

        {tab === 'published' && (
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">Published posts</h2>
              <button
                onClick={loadPublished}
                className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
              >
                {publishedLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
            {published.length === 0 ? (
              <p className="text-sm text-slate-500">No published posts yet.</p>
            ) : (
              <ul className="space-y-3">
                {published.map((p) => (
                  <li key={p.id} className="rounded-md border border-slate-200 p-3">
                    <div className="mb-1 flex items-center gap-2 text-xs text-slate-500">
                      <span className="rounded bg-slate-900 px-2 py-0.5 text-[10px] font-semibold uppercase text-white">
                        {PLATFORM_LABELS[p.platform]}
                      </span>
                      {p.content_pillar && (
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-700">
                          {PILLAR_LABELS[p.content_pillar]}
                        </span>
                      )}
                      <span>{p.published_at?.slice(0, 16).replace('T', ' ')}</span>
                    </div>
                    <pre className="whitespace-pre-wrap break-words font-sans text-sm text-slate-800">
                      {p.content}
                    </pre>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {tab === 'voice' && (
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">
                Voice guide (read-only)
              </h2>
              {voiceGuide && (
                <span className="text-xs text-slate-500">version {voiceGuide.version}</span>
              )}
            </div>
            {voiceGuide ? (
              <>
                <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-800">
                  {voiceGuide.voice_guide}
                </pre>
                <h3 className="mt-6 mb-2 text-sm font-semibold text-slate-900">
                  Seed posts (permanent voice anchors)
                </h3>
                <ul className="space-y-3">
                  {voiceGuide.seed_posts.map((s, i) => (
                    <li key={i} className="rounded-md border border-slate-200 bg-slate-50 p-3">
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                        {s.title} · {s.pillar}
                      </div>
                      <pre className="whitespace-pre-wrap break-words font-sans text-xs text-slate-700">
                        {s.content}
                      </pre>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="text-sm text-slate-500">Loading…</p>
            )}
          </section>
        )}
      </div>
    </main>
  )
}
