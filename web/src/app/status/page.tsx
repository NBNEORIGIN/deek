'use client'

import { useEffect, useState, useCallback } from 'react'

// ─── Types ────────────────────────────────────────────────────────────────────

interface TestResults {
  passed: number | null
  failed: number | null
  errors: number | null
  last_run: string | null
}

interface EvalEntry {
  id: string
  passed: boolean
  score: number
  model_used?: string | null
  cost_usd?: number | null
}

interface EvalResults {
  passed: number | null
  failed: number | null
  suite: string | null
  model?: string | null
  last_run: string | null
  results?: EvalEntry[]
}

interface ApiKeys {
  anthropic: boolean
  deepseek: boolean
  openai: boolean
}

interface OllamaStatus {
  available: boolean
  active_model: string | null
  installed_models: string[]
  vram_warning: boolean
}

interface ProjectInfo {
  name: string
  loaded: boolean
  codebase_exists: boolean | null
  files_indexed: number | null
  watcher_active: boolean
  last_reindex: string | null
}

interface WiggumRun {
  run_id: string
  goal: string
  status: string
  iterations: number
  started_at: string
}

interface StatusData {
  api_status: string
  stale?: boolean
  commit_hash: string | null
  commit_message: string | null
  commit_time: string | null
  test_results: TestResults
  eval_results: EvalResults
  api_keys: ApiKeys
  ollama: OllamaStatus
  projects: ProjectInfo[]
  wiggum_runs: WiggumRun[]
  pending_approvals: number
  generated_at: string
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function Dot({ on, warn }: { on: boolean; warn?: boolean }) {
  const colour = warn ? 'bg-amber-400' : on ? 'bg-green-500' : 'bg-red-500'
  return <span className={`inline-block w-2 h-2 rounded-full ${colour} mr-1.5 flex-shrink-0`} />
}

function KeyPill({ label, set }: { label: string; set: boolean }) {
  return (
    <span className={`inline-flex items-center text-xs px-2 py-0.5 rounded ${
      set ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-500'
    }`}>
      <Dot on={set} />
      {label}
    </span>
  )
}

function relativeTime(isoStr: string | null): string {
  if (!isoStr) return '—'
  try {
    const d = new Date(isoStr)
    const seconds = Math.floor((Date.now() - d.getTime()) / 1000)
    if (seconds < 60) return `${seconds}s ago`
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
    return `${Math.floor(seconds / 86400)}d ago`
  } catch {
    return isoStr
  }
}

function formatTime(isoStr: string | null): string {
  if (!isoStr) return '—'
  try {
    return new Date(isoStr).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return isoStr
  }
}

function wiggumIcon(status: string): string {
  switch (status) {
    case 'complete':     return '✓'
    case 'running':      return '↻'
    case 'error':        return '✗'
    case 'max_iterations': return '⚠'
    default:             return '○'
  }
}

function wiggumColour(status: string): string {
  switch (status) {
    case 'complete':     return 'text-green-400'
    case 'running':      return 'text-blue-400'
    case 'error':        return 'text-red-400'
    case 'max_iterations': return 'text-amber-400'
    default:             return 'text-gray-400'
  }
}

// ─── Sections ─────────────────────────────────────────────────────────────────

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <div className="px-4 py-2 bg-gray-800 border-b border-gray-700">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{title}</span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function SystemCard({ data }: { data: StatusData }) {
  return (
    <Card title="System">
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-6 flex-wrap">
          <span className="flex items-center text-gray-300">
            <Dot on={data.api_status === 'ok'} /> API {data.api_status}
          </span>
          {data.commit_hash && (
            <span className="text-gray-400 font-mono text-xs">
              <span className="text-gray-500 mr-1">commit</span>
              <span className="text-blue-400">{data.commit_hash}</span>
              {data.commit_message && (
                <span className="text-gray-500 ml-2 hidden sm:inline">
                  {data.commit_message.slice(0, 60)}{data.commit_message.length > 60 ? '…' : ''}
                </span>
              )}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <KeyPill label="Anthropic" set={data.api_keys.anthropic} />
          <KeyPill label="DeepSeek"  set={data.api_keys.deepseek} />
          <KeyPill label="OpenAI"    set={data.api_keys.openai} />
        </div>
        <div className="flex items-center gap-2 text-gray-300">
          <Dot on={data.ollama.available} warn={data.ollama.vram_warning} />
          <span>
            {data.ollama.available
              ? <>Ollama: <span className="text-gray-100 font-mono text-xs">{data.ollama.active_model}</span>
                  {data.ollama.vram_warning && <span className="text-amber-400 ml-2 text-xs">⚠ VRAM</span>}
                </>
              : 'Ollama: offline'
            }
          </span>
        </div>
      </div>
    </Card>
  )
}

function TestsCard({ results }: { results: TestResults }) {
  return (
    <Card title="Tests">
      <div className="flex items-center gap-4 text-sm">
        {results.passed !== null ? (
          <>
            <span className="text-green-400 font-semibold">{results.passed} passed</span>
            <span className={results.failed ? 'text-red-400 font-semibold' : 'text-gray-500'}>
              {results.failed ?? 0} failed
            </span>
            {(results.errors ?? 0) > 0 && (
              <span className="text-amber-400">{results.errors} error{results.errors !== 1 ? 's' : ''}</span>
            )}
            <span className="text-gray-600 text-xs ml-auto">
              last run {formatTime(results.last_run)}
            </span>
          </>
        ) : (
          <span className="text-gray-500 text-xs">No test results cached yet — run pytest to populate</span>
        )}
      </div>
    </Card>
  )
}

function EvalCard({ results }: { results: EvalResults }) {
  const topFailures = (results.results || []).filter(item => !item.passed).slice(0, 3)
  return (
    <Card title="Evaluator">
      <div className="space-y-3 text-sm">
        {results.passed !== null ? (
          <>
            <div className="flex items-center gap-4">
              <span className="text-green-400 font-semibold">{results.passed} passed</span>
              <span className={results.failed ? 'text-red-400 font-semibold' : 'text-gray-500'}>
                {results.failed ?? 0} failed
              </span>
              {results.model && (
                <span className="ml-auto text-xs text-gray-500">{results.model}</span>
              )}
            </div>
            <div className="text-xs text-gray-500">
              {results.suite || 'suite'} · last run {formatTime(results.last_run)}
            </div>
            {topFailures.length > 0 && (
              <div className="space-y-1 rounded-lg border border-amber-800 bg-amber-950/40 px-3 py-2 text-xs text-amber-200">
                {topFailures.map(item => (
                  <div key={item.id} className="flex items-center justify-between gap-3">
                    <span className="truncate">{item.id}</span>
                    <span className="text-amber-400">{Math.round(item.score * 100)}%</span>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : (
          <span className="text-gray-500 text-xs">No eval results cached yet — run the CLAW evaluator to populate</span>
        )}
      </div>
    </Card>
  )
}

function ProjectsCard({ projects }: { projects: ProjectInfo[] }) {
  const [indexingProject, setIndexingProject] = useState<string | null>(null)

  const triggerIndex = async (projectName: string) => {
    setIndexingProject(projectName)
    try {
      await fetch(`http://localhost:8765/projects/${projectName}/index`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      })
    } catch { /* ignore */ }
    setTimeout(() => setIndexingProject(null), 3000)
  }

  return (
    <Card title="Projects">
      {projects.length === 0 ? (
        <p className="text-gray-500 text-sm">No projects found</p>
      ) : (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-gray-800">
            {projects.map(p => {
              const hasIndex = p.files_indexed !== null && p.files_indexed > 0
              return (
                <tr key={p.name} className="text-gray-300">
                  <td className="py-1.5 pr-4 font-mono text-gray-100 w-36">{p.name}</td>
                  <td className="py-1.5 pr-4">
                    <span className="flex items-center gap-1">
                      <Dot on={p.loaded} />
                      <span className="text-gray-500 text-xs">{p.loaded ? 'loaded' : 'not loaded'}</span>
                    </span>
                  </td>
                  <td className="py-1.5 pr-4 text-xs">
                    {p.files_indexed !== null
                      ? <span className={hasIndex ? 'text-gray-400' : 'text-amber-400'}>{p.files_indexed} files indexed</span>
                      : <span className="text-gray-600">not indexed</span>
                    }
                  </td>
                  <td className="py-1.5 pr-4">
                    <span className="flex items-center gap-1 text-xs">
                      <Dot on={p.watcher_active} />
                      <span className="text-gray-500">watcher</span>
                    </span>
                  </td>
                  <td className="py-1.5 pr-2 text-gray-600 text-xs">
                    {p.last_reindex ? relativeTime(p.last_reindex) : 'never'}
                  </td>
                  <td className="py-1.5 text-right">
                    {!hasIndex && p.loaded ? (
                      <button
                        onClick={() => triggerIndex(p.name)}
                        disabled={indexingProject === p.name}
                        className="text-xs px-2 py-0.5 rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50"
                      >
                        {indexingProject === p.name ? 'Starting...' : 'Index now'}
                      </button>
                    ) : hasIndex ? (
                      <span className="text-green-500 text-xs">&#10003;</span>
                    ) : null}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}

function WiggumCard({ runs }: { runs: WiggumRun[] }) {
  return (
    <Card title="WIGGUM Runs">
      {runs.length === 0 ? (
        <p className="text-gray-500 text-sm">No runs yet</p>
      ) : (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-gray-800">
            {runs.map(r => (
              <tr key={r.run_id} className="text-gray-300">
                <td className={`py-1.5 pr-3 font-mono text-base ${wiggumColour(r.status)}`}>
                  {wiggumIcon(r.status)}
                </td>
                <td className="py-1.5 pr-4 text-gray-200 max-w-xs truncate">{r.goal}</td>
                <td className="py-1.5 pr-4">
                  <span className={`text-xs ${wiggumColour(r.status)}`}>{r.status}</span>
                </td>
                <td className="py-1.5 pr-4 text-gray-500 text-xs">
                  {r.iterations} iter{r.iterations !== 1 ? 's' : ''}
                </td>
                <td className="py-1.5 text-gray-600 text-xs text-right">
                  {relativeTime(r.started_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function StatusPage() {
  const [data, setData] = useState<StatusData | null>(null)
  const [offline, setOffline] = useState(false)
  const [stale, setStale] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/status', { cache: 'no-store' })
      if (!r.ok) {
        setOffline(true)
        return
      }
      const json = await r.json() as StatusData & { error?: string }
      if (json.error) {
        setOffline(true)
        setStale(false)
        return
      }
      setData(json)
      setOffline(false)
      setStale(Boolean(json.stale || json.api_status === 'stale'))
      setLastUpdated(new Date())
    } catch {
      if (!data) {
        setOffline(true)
      }
      setStale(Boolean(data))
    }
  }, [data])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 10_000)
    return () => clearInterval(id)
  }, [fetchStatus])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-4 sm:p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-gray-100">CLAW Status</h1>
          <a href="/" className="text-xs text-gray-600 hover:text-gray-400 transition-colors">
            ← Chat
          </a>
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          {offline
            ? <span className="text-red-400">● Offline</span>
            : stale
              ? <span className="text-amber-400">● Stale</span>
            : <span className="text-green-400">● Live</span>
          }
          {lastUpdated && (
            <span>Last updated {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
          )}
        </div>
      </div>

      {/* Offline banner */}
      {offline && (
        <div className="mb-4 px-4 py-3 bg-red-900 border border-red-700 rounded-lg text-red-200 text-sm">
          API offline — retrying every 10 seconds…
        </div>
      )}

      {stale && !offline && (
        <div className="mb-4 rounded-lg border border-amber-700 bg-amber-900 px-4 py-3 text-sm text-amber-200">
          Showing last known status — the API was slow to answer the latest poll.
        </div>
      )}

      {/* Pending approvals banner */}
      {data && data.pending_approvals > 0 && (
        <div className="mb-4 flex items-center justify-between px-4 py-3 bg-amber-900 border border-amber-700 rounded-lg">
          <span className="text-amber-200 text-sm">
            ⚠ {data.pending_approvals} pending approval{data.pending_approvals !== 1 ? 's' : ''}
          </span>
          <a href="/approvals" className="text-xs text-amber-300 hover:text-amber-100 underline">
            Review now →
          </a>
        </div>
      )}

      {/* Loading state */}
      {!data && !offline && (
        <div className="text-gray-600 text-sm">Loading…</div>
      )}

      {/* Dashboard grid */}
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <SystemCard data={data} />
            <TestsCard results={data.test_results} />
          </div>
          <EvalCard results={data.eval_results} />
          <ProjectsCard projects={data.projects} />
          <WiggumCard runs={data.wiggum_runs} />
        </div>
      )}
    </div>
  )
}
