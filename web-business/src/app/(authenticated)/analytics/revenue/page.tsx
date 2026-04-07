'use client'

import { useEffect, useState, useCallback } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'

// ── Types ─────────────────────────────────────────────────────────────────────

interface RevenueSummary {
  today: number
  yesterday: number
  vs_yesterday_pct: number | null
  wtd: number
  vs_last_week_pct: number | null
  mtd: number
  vs_last_month_pct: number | null
  ytd: number
  vs_last_year_pct: number | null
  marketplace: string
  source: string
  double_count_risk: boolean
}

interface RevenueRow {
  period: string
  marketplace: string
  units: number
  revenue: number
  currency: string
}

interface TopProduct {
  asin: string
  m_number: string | null
  product_name: string | null
  marketplace: string
  units: number
  revenue: number
  avg_price: number
  currency: string
}

interface Alert {
  id: number
  marketplace: string
  asin: string
  m_number: string | null
  alert: string
  velocity_7d: number | null
  velocity_7d_prior: number | null
  trend_pct: number | null
  computed_date: string
}

interface DataQuality {
  orders_last_synced: string | null
  orders_staleness: 'fresh' | 'stale' | 'very_stale' | 'never'
  orders_date_range: string | null
  orders_count: number
  traffic_last_synced: string | null
  traffic_count: number
  legacy_table_last_written: string | null
  double_count_risk: boolean
  explanation: string
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const MARKETPLACES = ['GB', 'US', 'CA', 'AU', 'DE', 'FR', 'IT', 'ES']

function fmtCurrency(v: number | null | undefined, currency = 'GBP') {
  if (v == null) return '—'
  const locale = currency === 'USD' || currency === 'CAD' ? 'en-US' : 'en-GB'
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(v)
}

function fmtPct(v: number | null | undefined) {
  if (v == null) return null
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(1)}%`
}

function relativeTime(iso: string | null) {
  if (!iso) return 'never'
  const d = new Date(iso)
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SummaryTile({
  label, value, pct, currency = 'GBP',
}: {
  label: string
  value: number | null
  pct?: number | null
  currency?: string
}) {
  const pctStr = fmtPct(pct)
  const isUp = pct != null && pct >= 0
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex flex-col gap-1">
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-900">{fmtCurrency(value, currency)}</p>
      {pctStr && (
        <p className={`text-xs font-medium ${isUp ? 'text-emerald-600' : 'text-red-500'}`}>
          {isUp ? '▲' : '▼'} {pctStr}
        </p>
      )}
    </div>
  )
}

function DataQualityBadge({
  quality, onClick,
}: {
  quality: DataQuality | null
  onClick: () => void
}) {
  if (!quality) return null
  const s = quality.orders_staleness
  const cfg = {
    fresh:      { dot: 'bg-emerald-500', text: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200', label: 'Data Quality ✓' },
    stale:      { dot: 'bg-amber-400',   text: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200',     label: 'Data Quality ⚠' },
    very_stale: { dot: 'bg-red-500',     text: 'text-red-700',     bg: 'bg-red-50 border-red-200',         label: 'Data Quality ✗' },
    never:      { dot: 'bg-slate-400',   text: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200',     label: 'No data yet' },
  }[s] ?? { dot: 'bg-slate-400', text: 'text-slate-600', bg: 'bg-slate-50 border-slate-200', label: 'Unknown' }

  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-medium ${cfg.bg} ${cfg.text} cursor-pointer`}
    >
      <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </button>
  )
}

function AlertBadge({ type }: { type: string }) {
  const cfg: Record<string, string> = {
    VELOCITY_DROP: 'bg-red-100 text-red-700',
    ZERO_DAYS:     'bg-amber-100 text-amber-700',
    SURGE:         'bg-emerald-100 text-emerald-700',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg[type] ?? 'bg-slate-100 text-slate-600'}`}>
      {type}
    </span>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RevenueDashboard() {
  const [marketplace, setMarketplace] = useState<string>('all')
  const [period, setPeriod] = useState<number>(30)
  const [summary, setSummary] = useState<RevenueSummary | null>(null)
  const [chartData, setChartData] = useState<RevenueRow[]>([])
  const [topProducts, setTopProducts] = useState<TopProduct[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [quality, setQuality] = useState<DataQuality | null>(null)
  const [showQualityModal, setShowQualityModal] = useState(false)
  const [loading, setLoading] = useState(true)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  const mkt = marketplace === 'all' ? undefined : marketplace

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const mktParam = mkt ? `&marketplace=${mkt}` : ''

      const [sumRes, chartRes, prodRes, alertRes, qualRes] = await Promise.all([
        fetch(`/api/analytics?path=/revenue/summary${mktParam}`),
        fetch(`/api/analytics?path=/revenue&start_date=${daysAgo(period)}&end_date=${today()}&group_by=day${mktParam}`),
        fetch(`/api/analytics?path=/top-products&days=${period}&limit=10${mktParam}`),
        fetch(`/api/analytics?path=/alerts&acknowledged=false${mktParam}`),
        fetch(`/api/analytics?path=/data-quality`),
      ])

      if (sumRes.ok) setSummary(await sumRes.json())
      if (chartRes.ok) {
        const d = await chartRes.json()
        // Aggregate by period if multiple marketplaces
        const agg: Record<string, number> = {}
        for (const row of d.rows ?? []) {
          const k = row.period?.slice(0, 10) ?? ''
          agg[k] = (agg[k] ?? 0) + (row.revenue ?? 0)
        }
        setChartData(
          Object.entries(agg)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([period, revenue]) => ({ period, revenue: Math.round(revenue) } as any))
        )
      }
      if (prodRes.ok) {
        const d = await prodRes.json()
        setTopProducts(d.products ?? [])
      }
      if (alertRes.ok) {
        const d = await alertRes.json()
        setAlerts(d.alerts ?? [])
      }
      if (qualRes.ok) setQuality(await qualRes.json())

      setLastRefresh(new Date())
    } finally {
      setLoading(false)
    }
  }, [mkt, period])

  useEffect(() => { load() }, [load])

  const dismissAlert = async (id: number) => {
    await fetch(`/api/analytics?path=/alerts/${id}/acknowledge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ acknowledged_by: 'web-ui' }),
    })
    setAlerts(prev => prev.filter(a => a.id !== id))
  }

  const currency = mkt ? ({ GB: 'GBP', US: 'USD', CA: 'CAD', AU: 'AUD', DE: 'EUR', FR: 'EUR', IT: 'EUR', ES: 'EUR' }[mkt] ?? 'GBP') : 'GBP'

  // Marketplace revenue for the bar breakdown
  const mktRevenue: Record<string, number> = {}
  for (const row of chartData) {
    // chartData is already aggregated — use top products for marketplace split
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-6">

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Revenue Dashboard</h1>
          {lastRefresh && (
            <p className="text-xs text-slate-400 mt-0.5">
              Updated {lastRefresh.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <DataQualityBadge quality={quality} onClick={() => setShowQualityModal(true)} />
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {loading ? (
              <span className="w-3 h-3 border border-slate-400 border-t-transparent rounded-full animate-spin" />
            ) : '↺'} Refresh
          </button>
        </div>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SummaryTile label="Today" value={summary?.today ?? null} pct={summary?.vs_yesterday_pct} currency={currency} />
        <SummaryTile label="This Week" value={summary?.wtd ?? null} pct={summary?.vs_last_week_pct} currency={currency} />
        <SummaryTile label="This Month" value={summary?.mtd ?? null} pct={summary?.vs_last_month_pct} currency={currency} />
        <SummaryTile label="YTD" value={summary?.ytd ?? null} pct={summary?.vs_last_year_pct} currency={currency} />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        {['all', ...MARKETPLACES].map(m => (
          <button
            key={m}
            onClick={() => setMarketplace(m)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
              marketplace === m
                ? 'bg-slate-800 text-white border-slate-800'
                : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400'
            }`}
          >
            {m === 'all' ? 'All ▼' : m}
          </button>
        ))}
        <span className="text-slate-300 mx-1">|</span>
        {[7, 30, 90].map(d => (
          <button
            key={d}
            onClick={() => setPeriod(d)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
              period === d
                ? 'bg-slate-800 text-white border-slate-800'
                : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400'
            }`}
          >
            {d === 7 ? 'Last 7 days' : d === 30 ? 'Last 30 days' : 'Last 90 days'}
          </button>
        ))}
      </div>

      {/* Revenue chart */}
      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <h2 className="text-sm font-medium text-slate-700 mb-4">Revenue by Day</h2>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                dataKey="period"
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                tickFormatter={(v) => v?.slice(5) ?? ''}
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                tickFormatter={(v) => `£${v >= 1000 ? `${(v/1000).toFixed(1)}k` : v}`}
                width={48}
              />
              <Tooltip
                formatter={(v: unknown) => [fmtCurrency(v as number, currency), 'Revenue']}
                labelFormatter={(l) => `Date: ${String(l)}`}
                contentStyle={{ fontSize: 12 }}
              />
              <Bar dataKey="revenue" fill="#1e293b" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-48 flex items-center justify-center text-sm text-slate-400">
            {loading ? 'Loading…' : 'No data for this period'}
          </div>
        )}
      </div>

      {/* Marketplace + top products */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Top products */}
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <h2 className="text-sm font-medium text-slate-700 mb-3">Top Products ({period}d)</h2>
          {topProducts.length > 0 ? (
            <div className="space-y-2">
              {topProducts.slice(0, 10).map((p, i) => (
                <div key={`${p.asin}-${p.marketplace}`} className="flex items-center gap-3">
                  <span className="text-xs text-slate-400 w-4 text-right">{i + 1}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium text-slate-700 truncate">
                      {p.m_number ? `${p.m_number} ` : ''}{p.product_name ?? p.asin}
                    </p>
                    <p className="text-[10px] text-slate-400">{p.marketplace} · {p.units}u</p>
                  </div>
                  <span className="text-xs font-semibold text-slate-800 whitespace-nowrap">
                    {fmtCurrency(p.revenue, p.currency ?? currency)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">{loading ? 'Loading…' : 'No orders yet'}</p>
          )}
        </div>

        {/* Alerts */}
        <div className="bg-white rounded-xl border border-slate-200 p-4">
          <h2 className="text-sm font-medium text-slate-700 mb-3">
            Alerts
            {alerts.length > 0 && (
              <span className="ml-2 bg-red-100 text-red-600 text-xs px-1.5 py-0.5 rounded-full">
                {alerts.length}
              </span>
            )}
          </h2>
          {alerts.length > 0 ? (
            <div className="space-y-2">
              {alerts.map(a => (
                <div key={a.id} className="flex items-start gap-2 p-2 rounded-lg bg-slate-50">
                  <AlertBadge type={a.alert} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-slate-700">
                      {a.m_number ?? a.asin} · {a.marketplace}
                    </p>
                    {a.velocity_7d != null && a.velocity_7d_prior != null && (
                      <p className="text-[10px] text-slate-400">
                        {a.velocity_7d_prior.toFixed(1)}/day → {a.velocity_7d.toFixed(1)}/day
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => dismissAlert(a.id)}
                    className="text-[10px] text-slate-400 hover:text-slate-600 shrink-0"
                  >
                    Dismiss
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-400">{loading ? 'Loading…' : 'No active alerts'}</p>
          )}
        </div>
      </div>

      {/* Data quality modal */}
      {showQualityModal && quality && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-md w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-900">Data Quality</h2>
              <button
                onClick={() => setShowQualityModal(false)}
                className="text-slate-400 hover:text-slate-600 text-lg"
              >✕</button>
            </div>
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <span className="text-slate-500">Orders last synced</span>
                <span className="font-medium">{relativeTime(quality.orders_last_synced)}</span>
                <span className="text-slate-500">Orders date range</span>
                <span className="font-medium">{quality.orders_date_range ?? '—'}</span>
                <span className="text-slate-500">Total order lines</span>
                <span className="font-medium">{quality.orders_count.toLocaleString()}</span>
                <span className="text-slate-500">Traffic last synced</span>
                <span className="font-medium">{relativeTime(quality.traffic_last_synced)}</span>
                <span className="text-slate-500">Traffic rows</span>
                <span className="font-medium">{quality.traffic_count.toLocaleString()}</span>
                <span className="text-slate-500">Legacy last written</span>
                <span className="font-medium text-emerald-600">{quality.legacy_table_last_written ? relativeTime(quality.legacy_table_last_written) : 'Never (retired)'}</span>
                <span className="text-slate-500">Double-count risk</span>
                <span className={`font-medium ${quality.double_count_risk ? 'text-red-600' : 'text-emerald-600'}`}>
                  {quality.double_count_risk ? 'Yes' : 'None'}
                </span>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed border-t pt-3">
                {quality.explanation}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
