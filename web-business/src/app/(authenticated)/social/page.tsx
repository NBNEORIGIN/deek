'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'

type Platform = 'facebook' | 'instagram' | 'linkedin'
type Pillar = 'job' | 'what_we_do' | 'team' | 'development'
type Mode = 'brief' | 'proofread'
type Tab = 'compose' | 'published' | 'voice'

interface Variant {
  id: number
  draft_id: number
  platform: Platform
  content: string
  generated_at: string
  generation_model: string
  revision_count: number
  parent_variant_id: number | null
  is_published: boolean
  published_at: string | null
  published_url: string | null
  cairn_memory_id: string | null
}

interface DraftResponse {
  draft_id: number
  detected_pillar: Pillar
  variants: Variant[]
  notes_for_jo?: string
  usage?: { model: string; input_tokens: number; output_tokens: number; cost_gbp: number }
}

interface PublishedItem {
  id: number
  draft_id: number
  platform: Platform
  content: string
  published_at: string
  published_url: string | null
  content_pillar?: Pillar | null
}

interface VoiceGuide {
  version: number
  voice_guide: string
  seed_posts: Array<{ platform: Platform; pillar: Pillar; title: string; content: string }>
}

const PLATFORMS: Platform[] = ['facebook', 'instagram', 'linkedin']

const PILLARS: Array<{ value: Pillar; label: string; hint: string }> = [
  { value: 'job', label: 'Job', hint: 'A finished piece of work' },
  { value: 'what_we_do', label: 'What we do', hint: 'Process or capability' },
  { value: 'team', label: 'Team', hint: 'People and culture' },
  { value: 'development', label: 'Development', hint: 'New machines, R&D, training' },
]

const PLATFORM_STYLES: Record<Platform, { label: string; dot: string }> = {
  facebook: { label: 'Facebook', dot: 'bg-blue-500' },
  instagram: { label: 'Instagram', dot: 'bg-pink-500' },
  linkedin: { label: 'LinkedIn', dot: 'bg-sky-700' },
}

function formatDate(iso?: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('en-GB', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function SocialPage() {
  const [tab, setTab] = useState<Tab>('compose')
  const [mode, setMode] = useState<Mode>('brief')
  const [brief, setBrief] = useState('')
  const [originalText, setOriginalText] = useState('')
  const [pillar, setPillar] = useState<Pillar>('job')
  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([...PLATFORMS])
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState('')
  const [response, setResponse] = useState<DraftResponse | null>(null)
  const [variantsByPlatform, setVariantsByPlatform] = useState<Record<Platform, Variant | null>>({
    facebook: null,
    instagram: null,
    linkedin: null,
  })
  const [refineText, setRefineText] = useState<Record<Platform, string>>({
    facebook: '',
    instagram: '',
    linkedin: '',
  })
  const [actingOn, setActingOn] = useState<number | null>(null)
  const [published, setPublished] = useState<PublishedItem[]>([])
  const [loadingPublished, setLoadingPublished] = useState(false)
  const [voiceGuide, setVoiceGuide] = useState<VoiceGuide | null>(null)

  const togglePlatform = (p: Platform) => {
    setSelectedPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p],
    )
  }

  const canGenerate = useMemo(() => {
    if (selectedPlatforms.length === 0) return false
    if (mode === 'brief') return brief.trim().length > 0
    return originalText.trim().length > 0
  }, [mode, brief, originalText, selectedPlatforms])

  const storeVariants = (variants: Variant[]) => {
    const next: Record<Platform, Variant | null> = {
      facebook: null,
      instagram: null,
      linkedin: null,
    }
    for (const v of variants) next[v.platform] = v
    setVariantsByPlatform(next)
  }

  const handleGenerate = async () => {
    if (!canGenerate) return
    setGenerating(true)
    setGenError('')
    setResponse(null)
    try {
      const endpoint = mode === 'brief' ? '/api/social/drafts' : '/api/social/drafts/proofread'
      const body =
        mode === 'brief'
          ? { brief, platforms: selectedPlatforms, content_pillar: pillar }
          : { original_text: originalText, platforms: selectedPlatforms, content_pillar: pillar }
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setGenError(err.error || err.detail || `Request failed (${res.status})`)
        return
      }
      const data: DraftResponse = await res.json()
      setResponse(data)
      storeVariants(data.variants)
    } catch {
      setGenError('Network error talking to Cairn.')
    } finally {
      setGenerating(false)
    }
  }

  const handleRefine = async (variant: Variant) => {
    const instruction = refineText[variant.platform]?.trim()
    if (!instruction) return
    setActingOn(variant.id)
    try {
      const res = await fetch(`/api/social/variants/${variant.id}/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction }),
      })
      if (!res.ok) return
      const data = await res.json()
      setVariantsByPlatform((prev) => ({ ...prev, [variant.platform]: data.variant }))
      setRefineText((prev) => ({ ...prev, [variant.platform]: '' }))
    } finally {
      setActingOn(null)
    }
  }

  const handleRegenerate = async (variant: Variant) => {
    setActingOn(variant.id)
    try {
      const res = await fetch(`/api/social/variants/${variant.id}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!res.ok) return
      const data = await res.json()
      setVariantsByPlatform((prev) => ({ ...prev, [variant.platform]: data.variant }))
    } finally {
      setActingOn(null)
    }
  }

  const handlePublish = async (variant: Variant) => {
    const url = window.prompt(
      `Paste the published ${PLATFORM_STYLES[variant.platform].label} URL (optional — leave blank if you just want to mark it published):`,
      '',
    )
    if (url === null) return
    setActingOn(variant.id)
    try {
      const res = await fetch(`/api/social/variants/${variant.id}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ published_url: url || null }),
      })
      if (!res.ok) return
      const data = await res.json()
      setVariantsByPlatform((prev) => ({ ...prev, [variant.platform]: data.variant }))
    } finally {
      setActingOn(null)
    }
  }

  const handleCopy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      /* ignore */
    }
  }

  const loadPublished = useCallback(async () => {
    setLoadingPublished(true)
    try {
      const res = await fetch('/api/social/published')
      if (!res.ok) {
        setPublished([])
        return
      }
      const data = await res.json()
      setPublished(Array.isArray(data.published) ? data.published : [])
    } finally {
      setLoadingPublished(false)
    }
  }, [])

  const loadVoiceGuide = useCallback(async () => {
    try {
      const res = await fetch('/api/social/voice-guide')
      if (!res.ok) return
      const data = await res.json()
      setVoiceGuide(data)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    if (tab === 'published') loadPublished()
    if (tab === 'voice' && !voiceGuide) loadVoiceGuide()
  }, [tab, voiceGuide, loadPublished, loadVoiceGuide])

  const activeVariants = PLATFORMS.map((p) => variantsByPlatform[p]).filter(
    (v): v is Variant => v !== null,
  )

  return (
    <div className="max-w-6xl mx-auto space-y-4 md:space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-lg font-semibold text-slate-800">Social</h1>
        <p className="text-sm text-slate-500 mt-1">
          Draft from a brief, or paste your own copy for a light proofread. All drafts stay in your voice.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-200">
        {([
          { id: 'compose', label: 'Compose' },
          { id: 'published', label: 'Published' },
          { id: 'voice', label: 'Voice guide' },
        ] as Array<{ id: Tab; label: string }>).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={
              'px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ' +
              (tab === t.id
                ? 'bg-white text-indigo-700 border-x border-t border-slate-200 -mb-px'
                : 'text-slate-600 hover:text-slate-900')
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'compose' && (
        <div className="grid gap-4 md:gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          {/* Left: composer */}
          <section className="bg-white border border-slate-200 rounded-xl p-4 md:p-5 shadow-sm space-y-4">
            {/* Mode toggle */}
            <div className="flex gap-2">
              {(['brief', 'proofread'] as Mode[]).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={
                    'flex-1 px-3 py-2 text-sm font-medium rounded-lg border transition-colors min-h-[40px] ' +
                    (mode === m
                      ? 'bg-indigo-600 text-white border-indigo-600'
                      : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50')
                  }
                >
                  {m === 'brief' ? 'Draft from brief' : 'Proofread my post'}
                </button>
              ))}
            </div>

            {/* Input textarea */}
            {mode === 'brief' ? (
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                  Brief
                </label>
                <textarea
                  value={brief}
                  onChange={(e) => setBrief(e.target.value)}
                  placeholder="Tell me about the job, machine, team moment or development you want to post about…"
                  className="w-full text-sm text-slate-800 placeholder-slate-400 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-y"
                  style={{ minHeight: 160 }}
                />
              </div>
            ) : (
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                  Your post
                </label>
                <textarea
                  value={originalText}
                  onChange={(e) => setOriginalText(e.target.value)}
                  placeholder="Paste the post you've written. I'll tidy it up and adapt it per platform without changing your voice."
                  className="w-full text-sm text-slate-800 placeholder-slate-400 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300 resize-y"
                  style={{ minHeight: 160 }}
                />
              </div>
            )}

            {/* Pillar */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                Content pillar
              </label>
              <div className="flex flex-wrap gap-2">
                {PILLARS.map((p) => (
                  <button
                    key={p.value}
                    onClick={() => setPillar(p.value)}
                    title={p.hint}
                    className={
                      'px-3 py-1.5 text-xs font-medium rounded-full border transition-colors ' +
                      (pillar === p.value
                        ? 'bg-indigo-600 text-white border-indigo-600'
                        : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50')
                    }
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Platforms */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-slate-600 uppercase tracking-wide">
                Platforms
              </label>
              <div className="flex flex-wrap gap-2">
                {PLATFORMS.map((p) => {
                  const on = selectedPlatforms.includes(p)
                  return (
                    <button
                      key={p}
                      onClick={() => togglePlatform(p)}
                      className={
                        'px-3 py-1.5 text-xs font-medium rounded-full border transition-colors flex items-center gap-1.5 ' +
                        (on
                          ? 'bg-slate-800 text-white border-slate-800'
                          : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50')
                      }
                    >
                      <span className={'w-1.5 h-1.5 rounded-full ' + PLATFORM_STYLES[p].dot} />
                      {PLATFORM_STYLES[p].label}
                    </button>
                  )
                })}
              </div>
            </div>

            {genError && (
              <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                {genError}
              </p>
            )}

            <button
              onClick={handleGenerate}
              disabled={!canGenerate || generating}
              className="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              {generating
                ? mode === 'brief'
                  ? 'Drafting…'
                  : 'Proofreading…'
                : mode === 'brief'
                  ? 'Draft posts'
                  : 'Proofread'}
            </button>

            {response?.usage && (
              <p className="text-xs text-slate-400">
                {response.usage.model} · {response.usage.input_tokens} in / {response.usage.output_tokens} out · £
                {response.usage.cost_gbp.toFixed(4)}
              </p>
            )}
            {response?.notes_for_jo && (
              <div className="text-xs text-slate-600 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 whitespace-pre-wrap">
                <span className="font-semibold text-amber-800">Notes:</span> {response.notes_for_jo}
              </div>
            )}
          </section>

          {/* Right: variants */}
          <section className="space-y-4">
            {activeVariants.length === 0 ? (
              <div className="bg-white border border-dashed border-slate-200 rounded-xl p-8 text-center">
                <p className="text-sm text-slate-400">
                  Drafts will appear here once you generate or proofread.
                </p>
              </div>
            ) : (
              activeVariants.map((v) => (
                <div
                  key={v.id}
                  className="bg-white border border-slate-200 rounded-xl shadow-sm overflow-hidden"
                >
                  <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
                    <div className="flex items-center gap-2">
                      <span className={'w-2 h-2 rounded-full ' + PLATFORM_STYLES[v.platform].dot} />
                      <span className="text-sm font-medium text-slate-800">
                        {PLATFORM_STYLES[v.platform].label}
                      </span>
                      {v.revision_count > 0 && (
                        <span className="text-[10px] uppercase tracking-wide font-medium text-slate-400">
                          rev {v.revision_count}
                        </span>
                      )}
                      {v.is_published && (
                        <span className="text-[10px] uppercase tracking-wide font-medium text-green-700 bg-green-50 border border-green-100 rounded-full px-2 py-0.5">
                          Published
                        </span>
                      )}
                    </div>
                    <button
                      onClick={() => handleCopy(v.content)}
                      className="text-xs text-slate-500 hover:text-slate-800"
                    >
                      Copy
                    </button>
                  </div>

                  <div className="px-4 py-3">
                    <p className="text-sm text-slate-800 whitespace-pre-wrap leading-relaxed">
                      {v.content}
                    </p>
                  </div>

                  <div className="px-4 pb-4 space-y-2 border-t border-slate-100 pt-3">
                    <div className="flex gap-2">
                      <input
                        value={refineText[v.platform]}
                        onChange={(e) =>
                          setRefineText((prev) => ({ ...prev, [v.platform]: e.target.value }))
                        }
                        placeholder="e.g. shorter, warmer, drop the hashtags…"
                        className="flex-1 text-xs text-slate-800 placeholder-slate-400 border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-300"
                      />
                      <button
                        onClick={() => handleRefine(v)}
                        disabled={actingOn === v.id || !refineText[v.platform]?.trim()}
                        className="px-3 py-2 text-xs font-medium bg-slate-800 hover:bg-slate-900 text-white rounded-lg disabled:opacity-40"
                      >
                        Refine
                      </button>
                    </div>
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => handleRegenerate(v)}
                        disabled={actingOn === v.id}
                        className="px-3 py-1.5 text-xs font-medium text-slate-600 hover:text-slate-900 disabled:opacity-40"
                      >
                        Regenerate
                      </button>
                      <button
                        onClick={() => handlePublish(v)}
                        disabled={actingOn === v.id || v.is_published}
                        className="px-3 py-1.5 text-xs font-medium text-green-700 hover:text-green-800 disabled:opacity-40"
                      >
                        {v.is_published ? 'Saved to memory' : 'Mark published'}
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </section>
        </div>
      )}

      {tab === 'published' && (
        <div className="space-y-3">
          {loadingPublished ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : published.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing published yet.</p>
          ) : (
            published.map((item) => (
              <div
                key={item.id}
                className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className={'w-2 h-2 rounded-full ' + PLATFORM_STYLES[item.platform].dot} />
                    <span className="text-sm font-medium text-slate-800">
                      {PLATFORM_STYLES[item.platform].label}
                    </span>
                    <span className="text-xs text-slate-400">{formatDate(item.published_at)}</span>
                  </div>
                  {item.published_url && (
                    <a
                      href={item.published_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                    >
                      View post ↗
                    </a>
                  )}
                </div>
                <p className="text-sm text-slate-700 whitespace-pre-wrap leading-relaxed">
                  {item.content}
                </p>
              </div>
            ))
          )}
        </div>
      )}

      {tab === 'voice' && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          {!voiceGuide ? (
            <p className="text-sm text-slate-400">Loading voice guide…</p>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-800">Voice guide</h2>
                <span className="text-xs text-slate-400">v{voiceGuide.version}</span>
              </div>
              <pre className="text-xs text-slate-700 whitespace-pre-wrap font-sans leading-relaxed">
                {voiceGuide.voice_guide}
              </pre>
              {voiceGuide.seed_posts?.length > 0 && (
                <div className="pt-3 border-t border-slate-100 space-y-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Anchor posts
                  </h3>
                  {voiceGuide.seed_posts.map((sp, idx) => (
                    <div key={idx} className="bg-slate-50 rounded-lg p-3">
                      <p className="text-xs font-medium text-slate-700 mb-1">
                        {sp.title}{' '}
                        <span className="text-slate-400">· {PLATFORM_STYLES[sp.platform].label}</span>
                      </p>
                      <p className="text-xs text-slate-600 whitespace-pre-wrap leading-relaxed">
                        {sp.content}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
