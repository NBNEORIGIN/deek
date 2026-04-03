'use client'

import { useEffect, useState, useCallback } from 'react'

// ---- Types ----------------------------------------------------------------

interface ContextData {
  ledger?: {
    cash_position?: { current_balance?: number }
    revenue?: { mtd?: number }
  }
  marketing?: {
    crm?: { pipeline_value?: number }
  }
  manufacture?: {
    stock_alerts?: unknown[]
    make_list?: MakeItem[]
  }
  modules?: ModuleStatus[]
}

interface MakeItem {
  id: string
  m_number: string
  description: string
  units_recommended: number
  reason: string
  priority_score: number
}

interface ModuleStatus {
  name: string
  status: 'ok' | 'warning' | 'error' | 'unavailable'
  summary: string
  last_updated: string
}

// ---- Helpers ---------------------------------------------------------------

function fmt_currency(v: number | undefined) {
  if (v === undefined || v === null) return '—'
  return new Intl.NumberFormat('en-GB', { style: 'currency', currency: 'GBP', maximumFractionDigits: 0 }).format(v)
}

function fmt_number(v: number | undefined) {
  if (v === undefined || v === null) return '—'
  return new Intl.NumberFormat('en-GB').format(v)
}

// ---- Sub-components --------------------------------------------------------

function MetricTile({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: string
  sub?: string
  accent?: string
}) {
  return (
    <div className={`bg-white rounded-xl border border-slate-200 p-4 md:p-5 flex flex-col gap-1 ${accent ?? ''}`}>
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  )
}

function StatusDot({ status }: { status: ModuleStatus['status'] }) {
  const map = {
    ok: 'bg-emerald-500',
    warning: 'bg-amber-400',
    error: 'bg-red-500',
    unavailable: 'bg-slate-300',
  }
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status]}`} />
}

// ---- Page ------------------------------------------------------------------

export default function DashboardPage() {
  const [data, setData] = useState<ContextData | null>(null)
  const [lastFetched, setLastFetched] = useState<Date | null>(null)

  const fetchContext = useCallback(async () => {
    try {
      const res = await fetch('/api/context', { credentials: 'include' })
      if (res.ok) {
        const json: ContextData = await res.json()
        setData(json)
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

  const cashBalance = data?.ledger?.cash_position?.current_balance
  const revenueMtd = data?.ledger?.revenue?.mtd
  const pipelineValue = data?.marketing?.crm?.pipeline_value
  const stockAlerts = data?.manufacture?.stock_alerts?.length ?? 0
  const makeList = (data?.manufacture?.make_list ?? []).filter((i) => i.priority_score > 0.8)
  const modules: ModuleStatus[] = data?.modules ?? []

  return (
    <div className="space-y-6 md:space-y-8 max-w-6xl">
      {/* KPI Row */}
      <section>
        <div className="grid grid-cols-2 gap-3 md:gap-4 md:grid-cols-4">
          <MetricTile label="Cash Position" value={fmt_currency(cashBalance)} sub="Current balance" />
          <MetricTile label="Revenue MTD" value={fmt_currency(revenueMtd)} sub="Month to date" />
          <MetricTile label="Pipeline Value" value={fmt_currency(pipelineValue)} sub="Open opportunities" />
          <MetricTile
            label="Stock Alerts"
            value={fmt_number(stockAlerts)}
            sub={stockAlerts === 1 ? 'item needs attention' : 'items need attention'}
            accent={stockAlerts > 0 ? 'border-amber-300' : ''}
          />
        </div>
      </section>

      {/* Priority Actions */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Priority Actions</h2>
        {makeList.length === 0 ? (
          <div className="bg-white rounded-xl border border-slate-200 px-4 py-6 md:px-5 md:py-8 text-center">
            <p className="text-sm text-slate-400">
              {data === null ? 'Loading…' : 'No urgent actions right now.'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {makeList.map((item) => (
              <div
                key={item.id}
                className="bg-white rounded-xl border border-slate-200 px-4 py-3 md:px-5 md:py-4 flex items-start gap-3 md:gap-4"
              >
                <div className="flex-shrink-0">
                  <span className="inline-block text-xs font-mono bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">
                    {item.m_number}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-slate-800">{item.description}</p>
                  <p className="text-xs text-slate-500 mt-0.5">
                    Make {item.units_recommended} units &mdash; {item.reason}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Module Status */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">System Overview</h2>
        {modules.length === 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4">
            {['Orders', 'Manufacturing', 'Finance', 'Stock', 'Customers', 'Fulfilment'].map((name) => (
              <div key={name} className="bg-white rounded-xl border border-slate-200 px-4 py-4">
                <div className="flex items-center gap-2 mb-1">
                  <StatusDot status="unavailable" />
                  <span className="text-sm font-medium text-slate-500">{name}</span>
                </div>
                <p className="text-xs text-slate-400">Not connected</p>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 md:gap-4">
            {modules.map((mod) => (
              <div key={mod.name} className="bg-white rounded-xl border border-slate-200 px-4 py-4">
                <div className="flex items-center gap-2 mb-1">
                  <StatusDot status={mod.status} />
                  <span className="text-sm font-medium text-slate-700">{mod.name}</span>
                </div>
                <p className="text-xs text-slate-500">{mod.summary}</p>
                {mod.last_updated && (
                  <p className="text-xs text-slate-400 mt-1">
                    Updated {new Date(mod.last_updated).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {lastFetched && (
        <p className="text-xs text-slate-300">
          Last updated {lastFetched.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </p>
      )}
    </div>
  )
}
