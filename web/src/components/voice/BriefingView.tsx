'use client'

/**
 * BriefingView — Deek's morning read.
 *
 * Shows the most recent pending briefing + the live on-demand briefing
 * (refreshable). Tasks are listed with tap-to-done actions.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Sparkles,
  Check,
  X,
  Edit3,
  Clock,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import type { VoiceLoopTurn } from '@/hooks/useVoiceLoop'

interface Task {
  id: number
  assignee: string
  content: string
  status: string
  source: string
  due_at: string | null
  title: string | null
  priority: string | null
  context: string | null
  linked_module: string | null
  linked_ref: string | null
}

interface BriefingResponse {
  user: string
  display_name: string | null
  role_tag: string | null
  generated_at: string
  briefing_md: string
  open_tasks: Task[]
  stale_snapshots: string[]
}

interface PendingBriefing {
  id: number
  email: string
  generated_at: string
  briefing_md: string
  seen_at: string | null
  dismissed_at: string | null
  incorrect_reason: string | null
}

interface DreamCandidate {
  id: string
  text: string
  type: 'pattern' | 'rule' | 'analogy' | 'prediction' | string
  confidence: number
  score: number
  source_memory_ids: number[]
  source_summaries: { memory_id: number; text: string }[]
  generated_at: string | null
  actions: string[]
}

interface DreamBriefing {
  date: string | null
  candidates: DreamCandidate[]
}

export function BriefingView({
  onTasksChanged,
}: {
  onTasksChanged?: () => void
}) {
  const [liveBriefing, setLiveBriefing] = useState<BriefingResponse | null>(null)
  const [pending, setPending] = useState<PendingBriefing[]>([])
  const [dream, setDream] = useState<DreamBriefing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionBusy, setActionBusy] = useState<number | null>(null)
  const [dreamBusy, setDreamBusy] = useState<string | null>(null)
  const [dreamToast, setDreamToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [bRes, pRes, dRes] = await Promise.all([
        fetch('/api/voice/briefing', { cache: 'no-store' }),
        fetch('/api/voice/briefings/pending', { cache: 'no-store' }),
        fetch('/api/voice/briefing/morning', { cache: 'no-store' }),
      ])
      if (bRes.ok) {
        setLiveBriefing(await bRes.json())
      } else if (bRes.status === 401) {
        window.location.href = '/voice/login?callbackUrl=/voice'
        return
      } else {
        setError(`Briefing failed: HTTP ${bRes.status}`)
      }
      if (pRes.ok) {
        const data = await pRes.json()
        // Mark the latest as seen when the user opens this view
        setPending(data.items || [])
        const unseen = (data.items || []).find((i: PendingBriefing) => !i.seen_at)
        if (unseen) {
          fetch(`/api/voice/briefings/${unseen.id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'seen' }),
          })
        }
      }
      if (dRes.ok) {
        setDream(await dRes.json())
      }
    } catch (err: any) {
      setError(err?.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const markDone = useCallback(
    async (taskId: number) => {
      setActionBusy(taskId)
      try {
        const res = await fetch(`/api/voice/tasks/${taskId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'done' }),
        })
        if (res.ok) {
          // Remove from the local list
          setLiveBriefing(b =>
            b ? { ...b, open_tasks: b.open_tasks.filter(t => t.id !== taskId) } : b
          )
          onTasksChanged?.()
        }
      } finally {
        setActionBusy(null)
      }
    },
    [onTasksChanged]
  )

  const reviewDream = useCallback(
    async (
      id: string,
      action: 'accept' | 'reject' | 'edit' | 'defer',
      opts: { edited_text?: string; notes?: string } = {},
    ) => {
      setDreamBusy(id)
      try {
        const res = await fetch(
          `/api/voice/briefing/candidate/${id}/review`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, ...opts }),
          },
        )
        if (res.ok) {
          const data = await res.json()
          // Remove the card from the local list with a toast
          setDream(d =>
            d
              ? { ...d, candidates: d.candidates.filter(c => c.id !== id) }
              : d,
          )
          const summary =
            action === 'accept' || action === 'edit'
              ? data.promoted_schema_id
                ? 'Promoted to memory'
                : 'Accepted (not embedded — check logs)'
              : action === 'reject'
              ? 'Rejected — filter will learn'
              : action === 'defer'
              ? 'Deferred to tomorrow'
              : 'Done'
          setDreamToast(summary)
          setTimeout(() => setDreamToast(null), 2500)
        } else {
          const data = await res.json().catch(() => ({}))
          setDreamToast(`Review failed: ${data?.detail || res.status}`)
          setTimeout(() => setDreamToast(null), 3500)
        }
      } finally {
        setDreamBusy(null)
      }
    },
    [],
  )

  const markIncorrect = useCallback(async (briefingId: number) => {
    const reason = prompt(
      'What was wrong with this briefing? (Optional — leave blank to just flag it)'
    )
    if (reason === null) return
    await fetch(`/api/voice/briefings/${briefingId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'incorrect',
        incorrect_reason: reason || '(no reason given)',
      }),
    })
    alert('Thanks — flagged. Toby will review.')
  }, [])

  if (loading && !liveBriefing) {
    return (
      <div className="flex h-full items-center justify-center bg-slate-950 text-slate-500">
        Loading briefing…
      </div>
    )
  }

  const latestPending = pending.find(p => !p.dismissed_at)

  return (
    <div className="h-full overflow-y-auto bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-2xl space-y-6 px-4 py-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-slate-500">
            {liveBriefing?.role_tag
              ? `${liveBriefing.role_tag} briefing`
              : 'Briefing'}
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1 rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-50"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {error && (
          <div className="rounded-lg bg-rose-950/60 px-3 py-2 text-sm text-rose-200">
            {error}
          </div>
        )}

        {/* Dream-state candidates (Brief 4 Phase B). Empty state only
            rendered when there was a run but nothing survived the
            filter — the "memory is the product" line. */}
        {dream && (
          <section className="space-y-2">
            <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-violet-400">
              <Sparkles size={12} />
              Overnight
              {dream.date && (
                <span className="text-slate-500 normal-case tracking-normal">
                  · {dream.date}
                </span>
              )}
            </div>
            {dream.candidates.length === 0 ? (
              <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3 text-xs text-slate-500">
                {dream.date
                  ? 'No candidates survived the filter. Memory is the product — some nights there\'s nothing worth saying.'
                  : 'No overnight run yet. The nocturnal loop fires at 02:30 UTC.'}
              </div>
            ) : (
              <div className="space-y-2">
                {dream.candidates.map(c => (
                  <DreamCard
                    key={c.id}
                    candidate={c}
                    busy={dreamBusy === c.id}
                    onAction={(action, opts) => reviewDream(c.id, action, opts)}
                  />
                ))}
              </div>
            )}
          </section>
        )}

        {dreamToast && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 rounded-lg bg-slate-800 px-4 py-2 text-xs text-slate-100 shadow-xl">
            {dreamToast}
          </div>
        )}

        {/* Most recent pending briefing (if distinct from live) */}
        {latestPending && (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
            <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
              <span>
                Delivered{' '}
                {new Date(latestPending.generated_at).toLocaleString('en-GB', {
                  weekday: 'short',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
              <button
                onClick={() => markIncorrect(latestPending.id)}
                className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300"
                title="Flag as incorrect"
              >
                <AlertTriangle size={12} />
                Flag
              </button>
            </div>
            <MarkdownBlock md={latestPending.briefing_md} />
          </div>
        )}

        {/* Live on-demand briefing (fresh snapshot) */}
        {liveBriefing && (
          <div className="rounded-2xl border border-emerald-900/60 bg-emerald-950/20 p-4">
            <div className="mb-2 text-xs text-emerald-400">
              Live — generated just now
            </div>
            <MarkdownBlock md={liveBriefing.briefing_md} />

            {liveBriefing.open_tasks.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-xs uppercase tracking-wider text-slate-500">
                  Your tasks
                </div>
                {liveBriefing.open_tasks.map(t => (
                  <div
                    key={t.id}
                    className="flex items-start justify-between gap-3 rounded-lg bg-slate-900 px-3 py-2 text-sm"
                  >
                    <div className="flex-1">
                      {t.priority && (
                        <span
                          className={`mr-2 inline-block rounded-full px-1.5 py-0.5 text-[10px] uppercase ${
                            t.priority === 'critical'
                              ? 'bg-rose-900 text-rose-200'
                              : t.priority === 'high'
                              ? 'bg-amber-900 text-amber-200'
                              : 'bg-slate-800 text-slate-300'
                          }`}
                        >
                          {t.priority}
                        </span>
                      )}
                      {t.title || t.content}
                      {t.due_at && (
                        <div className="mt-0.5 text-xs text-slate-500">
                          Due {new Date(t.due_at).toLocaleDateString('en-GB')}
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => markDone(t.id)}
                      disabled={actionBusy === t.id}
                      className="flex items-center gap-1 rounded-md bg-emerald-700 px-2 py-1 text-xs text-white hover:bg-emerald-600 disabled:opacity-50"
                    >
                      <CheckCircle2 size={12} />
                      Done
                    </button>
                  </div>
                ))}
              </div>
            )}

            {liveBriefing.stale_snapshots.length > 0 && (
              <div className="mt-4 rounded-md bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
                ⚠ Snapshot data for {liveBriefing.stale_snapshots.join(', ')} is
                older than 2h — figures may be out of date.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function DreamCard({
  candidate,
  busy,
  onAction,
}: {
  candidate: DreamCandidate
  busy: boolean
  onAction: (
    action: 'accept' | 'reject' | 'edit' | 'defer',
    opts?: { edited_text?: string; notes?: string },
  ) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(candidate.text)

  return (
    <div className="rounded-xl border border-violet-900/40 bg-violet-950/20 p-4">
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="rounded-full bg-violet-900/50 px-2 py-0.5 uppercase tracking-wider text-violet-200">
          {candidate.type}
        </span>
        <span className="text-slate-500">
          confidence {Math.round(candidate.confidence * 100)}%
        </span>
      </div>

      {editing ? (
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          rows={3}
          className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
        />
      ) : (
        <div className="mb-3 text-sm leading-relaxed text-slate-100">
          {candidate.text}
        </div>
      )}

      {candidate.source_summaries.length > 0 && (
        <button
          onClick={() => setExpanded(x => !x)}
          className="mb-2 flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {candidate.source_summaries.length} source{' '}
          {candidate.source_summaries.length === 1 ? 'memory' : 'memories'}
        </button>
      )}

      {expanded && (
        <div className="mb-3 space-y-1 rounded-md bg-slate-900/60 p-2 text-xs text-slate-400">
          {candidate.source_summaries.map(s => (
            <div key={s.memory_id}>
              <span className="text-slate-600">#{s.memory_id}</span> {s.text}
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {editing ? (
          <>
            <button
              onClick={() => onAction('edit', { edited_text: draft })}
              disabled={busy || !draft.trim()}
              className="flex items-center gap-1 rounded-md bg-emerald-700 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              <Check size={12} /> Save & accept
            </button>
            <button
              onClick={() => {
                setEditing(false)
                setDraft(candidate.text)
              }}
              className="flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => onAction('accept')}
              disabled={busy}
              className="flex items-center gap-1 rounded-md bg-emerald-700 px-3 py-1 text-xs text-white hover:bg-emerald-600 disabled:opacity-50"
            >
              <Check size={12} /> Accept
            </button>
            <button
              onClick={() => onAction('reject')}
              disabled={busy}
              className="flex items-center gap-1 rounded-md bg-rose-900 px-3 py-1 text-xs text-rose-100 hover:bg-rose-800 disabled:opacity-50"
            >
              <X size={12} /> Reject
            </button>
            <button
              onClick={() => setEditing(true)}
              disabled={busy}
              className="flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-50"
            >
              <Edit3 size={12} /> Edit
            </button>
            <button
              onClick={() => onAction('defer')}
              disabled={busy}
              className="flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500 disabled:opacity-50"
            >
              <Clock size={12} /> Defer
            </button>
          </>
        )}
      </div>
    </div>
  )
}


function MarkdownBlock({ md }: { md: string }) {
  // Lightweight markdown rendering — just handle headings, bold, bullet
  // lists. Good enough for briefings (no tables or code).
  const html = md
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/^## (.+)$/gm, '<h3 class="text-base font-semibold mt-2 mb-1 text-slate-100">$1</h3>')
    .replace(/^# (.+)$/gm, '<h2 class="text-lg font-semibold mt-3 mb-2 text-slate-50">$1</h2>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong class="text-slate-100">$1</strong>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc text-slate-200">$1</li>')
    .replace(/\n\n/g, '</p><p class="my-2">')
    .replace(/\n/g, '<br/>')
  return (
    <div
      className="prose-invert text-sm leading-relaxed text-slate-300"
      dangerouslySetInnerHTML={{ __html: `<p class="my-1">${html}</p>` }}
    />
  )
}
