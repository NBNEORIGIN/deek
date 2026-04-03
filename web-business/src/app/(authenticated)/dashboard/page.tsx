'use client'

import { useEffect, useState, useCallback } from 'react'

// ---- Types ----------------------------------------------------------------

interface ModuleResult {
  key: string
  name: string
  status: 'live' | 'stale' | 'unavailable'
  generated_at: string
  summary: string
  data: Record<string, unknown> | null
}

type ContextResponse = Record<string, ModuleResult>

// ---- Helpers ---------------------------------------------------------------

function fmt_currency(v: number | undefined | null) {
  if (v === undefined || v === null) return '—'
  return new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 }).format(v)
}

function fmt_number(v: number | undefined | null) {
  if (v === undefined || v === null) return '—'
  return new Intl.NumberFormat('en-GB').format(v)
}

function fmt_pct(v: number | undefined | null) {
  if (v === undefined || v === null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

// Extract nested value safely
function dig(obj: Record<string, unknown> | null, ...keys: string[]): unknown {
  let current: unknown = obj
  for (const key of keys) {
    if (current == null || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

// ---- Sub-components --------------------------------------------------------

function MetricTile({ label, value, sub, accent }: {
  label: string; value: string; sub?: string; accent?: string
}) {
  return (
    <div className={`bg-white rounded-xl border border-slate-200 p-4 md:p-5 flex flex-col gap-1 ${accent ?? ''}`}>
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = {
    live: 'bg-emerald-500',
    stale: 'bg-amber-400',
    unavailable: 'bg-slate-300',
  }
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status] ?? map.unavailable}`} />
}

function ModuleCard({ mod }: { mod: ModuleResult }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 px-4 py-4">
      <div className="flex items-center gap-2 mb-1">
        <StatusDot status={mod.status} />
        <span className="text-sm font-medium text-slate-700">{mod.name}</span>
      </div>
      {mod.status === 'unavailable' ? (
        <p className="text-xs text-slate-400">Not connected</p>
      ) : (
        <>
          <p className="text-xs text-slate-500">{mod.summary}</p>
          {mod.generated_at && (
            <p className="text-xs text-slate-400 mt-1">
              Updated {new Date(mod.generated_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}
        </>
      )}
    </div>
  )
}

// ---- KPI extraction from module data ----------------------------------------

function extractKPIs(data: ContextResponse) {
  // Finance / Ledger
  const cashBalance = dig(data.ledger?.data, 'cash_position', 'current_balance') as number | undefined
  const revenueMtd = dig(data.ledger?.data, 'revenue', 'mtd') as number | undefined

  // Marketing / CRM
  const pipelineValue = dig(data.marketing?.data, 'crm', 'pipeline_value') as number | undefined

  // Amazon Intelligence
  const amiData = data.amazon?.data as Record<string, unknown> | null
  const amiSummary = amiData?.summary as Record<string, unknown> | null
  const amiCritical = (amiSummary?.critical as number) ?? 0
  const amiAttention = (amiSummary?.attention as number) ?? 0
  const amiTotal = (amiSummary?.total as number) ?? 0
  const amiQuickWins = amiData?.quick_wins as Record<string, unknown> | null
  const imagesNeeded = (amiQuickWins?.images_needed as number) ?? 0
  const bulletsNeeded = (amiQuickWins?.bullets_needed as number) ?? 0
  const amiMarginAlerts = (amiData?.margin_alerts as number) ?? 0

  // Manufacturing
  const stockAlerts = (dig(data.manufacture?.data, 'stock_alerts') as unknown[] | undefined)?.length ?? 0
  const makeList = (dig(data.manufacture?.data, 'make_list') as Array<Record<string, unknown>> | undefined) ?? []
  const priorityItems = makeList.filter((i) => ((i.priority_score as number) ?? 0) > 0.8)

  return {
    cashBalance, revenueMtd, pipelineValue,
    amiCritical, amiAttention, amiTotal, imagesNeeded, bulletsNeeded, amiMarginAlerts,
    stockAlerts, priorityItems,
  }
}

// ---- Priority actions from all modules ------------------------------------

interface PriorityAction {
  id: string
  source: string
  label: string
  detail: string
  accent?: string
}

function extractPriorityActions(data: ContextResponse): PriorityAction[] {
  const actions: PriorityAction[] = []

  // Amazon Intelligence — critical listings
  const amiData = data.amazon?.data as Record<string, unknown> | null
  if (amiData) {
    const summary = amiData.summary as Record<string, unknown> | null
    const critical = (summary?.critical as number) ?? 0
    const attention = (summary?.attention as number) ?? 0
    if (critical > 0) {
      actions.push({
        id: 'ami-critical',
        source: 'Amazon',
        label: `${critical} critical listing${critical > 1 ? 's' : ''} need attention`,
        detail: `${attention} more need review. Run the weekly report for full diagnosis.`,
        accent: 'border-l-4 border-l-red-400',
      })
    }
    const topIssues = (amiData.top_issues as Array<Record<string, unknown>>) ?? []
    if (topIssues.length > 0) {
      const top = topIssues[0]
      actions.push({
        id: 'ami-top-issue',
        source: 'Amazon',
        label: `${top.count} listings: ${String(top.code ?? '').replace(/_/g, ' ').toLowerCase()}`,
        detail: 'Most common listing issue across your catalogue.',
      })
    }
    const marginAlerts = (amiData.margin_alerts as number) ?? 0
    if (marginAlerts > 0) {
      actions.push({
        id: 'ami-margins',
        source: 'Amazon',
        label: `${marginAlerts} margin alert${marginAlerts > 1 ? 's' : ''}`,
        detail: 'Products where ad spend exceeds margin threshold.',
        accent: 'border-l-4 border-l-amber-400',
      })
    }
  }

  // Manufacturing — priority make list items
  const mfgData = data.manufacture?.data as Record<string, unknown> | null
  if (mfgData) {
    const makeList = (mfgData.make_list as Array<Record<string, unknown>>) ?? []
    const urgent = makeList.filter((i) => ((i.priority_score as number) ?? 0) > 0.8)
    for (const item of urgent.slice(0, 5)) {
      actions.push({
        id: `mfg-${item.m_number ?? item.id}`,
        source: 'Manufacturing',
        label: `Make ${item.units_recommended} × ${item.m_number ?? 'unknown'}`,
        detail: `${item.description ?? ''} — ${item.reason ?? ''}`,
      })
    }
  }

  return actions
}

// ---- Page ------------------------------------------------------------------

export default function DashboardPage() {
  const [data, setData] = useState<ContextResponse | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)

  const fetchContext = useCallback(async () => {
    try {
      const res = await fetch('/api/context', { credentials: 'include' })
      if (res.ok) {
        setData(await res.json())
        setLastFetched(new Date())
      }
    } catch {
      // silently retain stale data
    }
  }, [])

  useEffect(() => {
    fetchContext()
    const interval = setInterval(fetchContext, 30_000)
    return () => clearInterval(interval)
  }, [fetchContext])

  const modules = data ? Object.values(data) : []
  const kpis = data ? extractKPIs(data) : null
  const actions = data ? extractPriorityActions(data) : []

  return (
    <div className="space-y-6 md:space-y-8 max-w-6xl">
      {/* KPI Row */}
      <section>
        <div className="grid grid-cols-2 gap-3 md:gap-4 md:grid-cols-4">
          <MetricTile label="Cash Position" value={fmt_currency(kpis?.cashBalance)} sub="Current balance" />
          <MetricTile label="Revenue MTD" value={fmt_currency(kpis?.revenueMtd)} sub="Month to date" />
          <MetricTile
            label="Amazon Listings"
            value={fmt_number(kpis?.amiTotal || undefined)}
            sub={kpis?.amiCritical ? `${kpis.amiCritical} critical` : 'Analysed'}
            accent={kpis?.amiCritical ? 'border-amber-300' : ''}
          />
          <MetricTile
            label="Quick Wins"
            value={fmt_number((kpis?.imagesNeeded ?? 0) + (kpis?.bulletsNeeded ?? 0) || undefined)}
            sub="Images + bullets to add"
          />
        </div>
      </section>

      {/* Priority Actions */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Priority Actions</h2>
        {actions.length === 0 ? (
          <div className="bg-white rounded-xl border border-slate-200 px-4 py-6 md:px-5 md:py-8 text-center">
            <p className="text-sm text-slate-400">
              {data === null ? 'Loading…' : 'No urgent actions right now.'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {actions.map((action) => (
              <div
                key={action.id}
                className={`bg-white rounded-xl border border-slate-200 px-4 py-3 md:px-5 md:py-4 flex items-start gap-3 md:gap-4 ${action.accent ?? ''}`}
              >
                <div className="flex-shrink-0">
                  <span className="inline-block text-xs font-medium bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                    {action.source}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-800">{action.label}</p>
                  <p className="text-xs text-slate-500 mt-0.5">{action.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Module Status — auto-discovered */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">System Overview</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
          {modules.length === 0
            ? ['Loading…'].map((name) => (
                <div key={name} className="bg-white rounded-xl border border-slate-200 px-4 py-4">
                  <p className="text-xs text-slate-400">{name}</p>
                </div>
              ))
            : modules.map((mod) => <ModuleCard key={mod.key} mod={mod} />)
          }
        </div>
      </section>

      {lastFetched && (
        <p className="text-xs text-slate-300">
          Last updated {lastFetched.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </p>
      )}
    </div>
  )
}
