'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface MarginRow {
  asin: string
  marketplace: string
  m_number: string | null
  units: number
  gross_revenue: number
  net_revenue: number
  fees_per_unit: number | null
  fees_total: number | null
  cogs_per_unit: number | null
  cogs_total: number | null
  ad_spend: number
  gross_profit: number | null
  gross_margin_pct: number | null
  net_profit: number | null
  net_margin_pct: number | null
  fee_source: string | null
  cost_source: string | null
  is_composite: boolean
  confidence: 'HIGH' | 'MEDIUM' | 'LOW'
}

interface Summary {
  total_skus: number
  scored_skus: number
  buckets: { healthy: number; thin: number; unprofitable: number; unknown: number }
  total_net_revenue: number
  total_net_profit: number
}

interface MarginResponse {
  marketplace: string
  lookback_days: number
  summary: Summary
  results: MarginRow[]
}

// ── Constants ─────────────────────────────────────────────────────────────────

const MARKETPLACES: { code: string; label: string }[] = [
  { code: 'UK', label: '🇬🇧 UK' },
  { code: 'DE', label: '🇩🇪 Germany' },
  { code: 'FR', label: '🇫🇷 France' },
  { code: 'IT', label: '🇮🇹 Italy' },
  { code: 'ES', label: '🇪🇸 Spain' },
  { code: 'US', label: '🇺🇸 US' },
  { code: 'CA', label: '🇨🇦 Canada' },
  { code: 'AU', label: '🇦🇺 Australia' },
]

const LOOKBACK_CHOICES = [
  { days: 7, label: '7d' },
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
]

type SortKey = keyof MarginRow
type SortDir = 'asc' | 'desc'

interface ColumnDef {
  key: SortKey
  label: string
  numeric?: boolean
  format?: (v: unknown, row: MarginRow) => string
  align?: 'left' | 'right'
}

const CURRENCY_BY_MARKETPLACE: Record<string, string> = {
  UK: 'GBP', DE: 'EUR', FR: 'EUR', IT: 'EUR', ES: 'EUR', NL: 'EUR',
  US: 'USD', CA: 'CAD', AU: 'AUD',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function money(v: number | null | undefined, marketplace: string): string {
  if (v === null || v === undefined) return '—'
  const currency = CURRENCY_BY_MARKETPLACE[marketplace] ?? 'GBP'
  try {
    return new Intl.NumberFormat('en-GB', {
      style: 'currency', currency, maximumFractionDigits: 2,
    }).format(v)
  } catch {
    return v.toFixed(2)
  }
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(1)}%`
}

function confidenceBadge(c: MarginRow['confidence']): { cls: string; label: string } {
  switch (c) {
    case 'HIGH':   return { cls: 'bg-emerald-100 text-emerald-700', label: 'HIGH' }
    case 'MEDIUM': return { cls: 'bg-amber-100 text-amber-700',   label: 'MED'  }
    case 'LOW':    return { cls: 'bg-rose-100 text-rose-700',     label: 'LOW'  }
    default:       return { cls: 'bg-slate-100 text-slate-700',   label: String(c) }
  }
}

function marginCellClass(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return 'text-slate-400'
  if (pct >= 20) return 'text-emerald-700 font-medium'
  if (pct >= 5) return 'text-amber-700'
  return 'text-rose-700 font-medium'
}

function compareVals(a: unknown, b: unknown, dir: SortDir): number {
  // Nulls always sort to the bottom regardless of direction.
  const aNull = a === null || a === undefined
  const bNull = b === null || b === undefined
  if (aNull && bNull) return 0
  if (aNull) return 1
  if (bNull) return -1
  if (typeof a === 'number' && typeof b === 'number') {
    return dir === 'asc' ? a - b : b - a
  }
  const sa = String(a).toLowerCase()
  const sb = String(b).toLowerCase()
  if (sa < sb) return dir === 'asc' ? -1 : 1
  if (sa > sb) return dir === 'asc' ? 1 : -1
  return 0
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ProfitabilityPage() {
  const [marketplace, setMarketplace] = useState('UK')
  const [lookback, setLookback] = useState(30)
  const [data, setData] = useState<MarginResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [onlyLoss, setOnlyLoss] = useState(false)
  const [minConfidence, setMinConfidence] = useState<'ANY' | 'MEDIUM' | 'HIGH'>('ANY')
  const [sortKey, setSortKey] = useState<SortKey>('net_profit')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const load = useCallback(async () => {
    setLoading(true); setErr(null)
    try {
      const res = await fetch(
        `/api/margin?path=/per-sku&marketplace=${marketplace}&lookback_days=${lookback}`,
        { cache: 'no-store' },
      )
      const j = await res.json()
      if (!res.ok) throw new Error(j?.detail || j?.error || 'Failed to load margins')
      setData(j as MarginResponse)
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [marketplace, lookback])

  useEffect(() => { load() }, [load])

  const columns: ColumnDef[] = useMemo(() => [
    { key: 'asin',            label: 'ASIN' },
    { key: 'm_number',        label: 'M#' },
    { key: 'units',           label: 'Units',      numeric: true, align: 'right' },
    { key: 'gross_revenue',   label: 'Gross rev',  numeric: true, align: 'right',
      format: (v) => money(v as number, marketplace) },
    { key: 'net_revenue',     label: 'Net rev',    numeric: true, align: 'right',
      format: (v) => money(v as number, marketplace) },
    { key: 'fees_total',      label: 'Fees',       numeric: true, align: 'right',
      format: (v) => money(v as number | null, marketplace) },
    { key: 'cogs_total',      label: 'COGS',       numeric: true, align: 'right',
      format: (v) => money(v as number | null, marketplace) },
    { key: 'ad_spend',        label: 'Ads',        numeric: true, align: 'right',
      format: (v) => money(v as number, marketplace) },
    { key: 'net_profit',      label: 'Net profit', numeric: true, align: 'right',
      format: (v) => money(v as number | null, marketplace) },
    { key: 'net_margin_pct',  label: 'Margin',     numeric: true, align: 'right',
      format: (v) => pct(v as number | null) },
    { key: 'confidence',      label: 'Conf' },
  ], [marketplace])

  const filteredRows = useMemo(() => {
    if (!data) return []
    const q = query.trim().toLowerCase()
    let rows = data.results.filter((r) => {
      if (q && !(r.asin.toLowerCase().includes(q)
                 || (r.m_number ?? '').toLowerCase().includes(q))) return false
      if (onlyLoss && (r.net_profit === null || r.net_profit >= 0)) return false
      if (minConfidence === 'HIGH' && r.confidence !== 'HIGH') return false
      if (minConfidence === 'MEDIUM' && r.confidence === 'LOW') return false
      return true
    })
    rows = rows.slice().sort((a, b) =>
      compareVals((a as unknown as Record<string, unknown>)[sortKey],
                  (b as unknown as Record<string, unknown>)[sortKey],
                  sortDir))
    return rows
  }, [data, query, onlyLoss, minConfidence, sortKey, sortDir])

  function headerClick(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  // Heuristic: when every scored row has the same COGS/unit, the costs engine
  // is on placeholder data. Flag prominently because profit numbers are noise.
  const costPlaceholderWarning = useMemo(() => {
    if (!data) return null
    const scored = data.results.filter(r => r.cogs_per_unit !== null)
    if (scored.length < 10) return null
    const unique = new Set(scored.map(r => r.cogs_per_unit))
    if (unique.size === 1) {
      const v = [...unique][0]
      return `All ${scored.length} SKUs show identical COGS (${money(v!, marketplace)}/unit). Blank costs in Manufacture haven’t been populated — profit figures are placeholders until they are.`
    }
    return null
  }, [data, marketplace])

  const s = data?.summary
  const buckets = s?.buckets

  return (
    <div className="mx-auto max-w-[1400px]">
      <header className="mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Profitability</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Per-SKU margin from orders, fees, COGS and ads. Sort any column. Use filters to drill in.
        </p>
      </header>

      {/* Controls */}
      <div className="bg-white rounded-lg border border-slate-200 p-3 flex flex-wrap items-center gap-3 mb-4">
        <label className="text-xs text-slate-600 flex items-center gap-2">
          Marketplace
          <select
            value={marketplace}
            onChange={(e) => setMarketplace(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {MARKETPLACES.map((m) => (
              <option key={m.code} value={m.code}>{m.label}</option>
            ))}
          </select>
        </label>

        <label className="text-xs text-slate-600 flex items-center gap-2">
          Lookback
          <select
            value={lookback}
            onChange={(e) => setLookback(Number(e.target.value))}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            {LOOKBACK_CHOICES.map((c) => (
              <option key={c.days} value={c.days}>{c.label}</option>
            ))}
          </select>
        </label>

        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter ASIN or M#…"
          className="flex-1 min-w-[160px] border border-slate-300 rounded px-2 py-1 text-sm"
        />

        <label className="text-xs text-slate-600 flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={onlyLoss}
            onChange={(e) => setOnlyLoss(e.target.checked)}
          />
          Loss-makers only
        </label>

        <label className="text-xs text-slate-600 flex items-center gap-2">
          Confidence ≥
          <select
            value={minConfidence}
            onChange={(e) => setMinConfidence(e.target.value as typeof minConfidence)}
            className="border border-slate-300 rounded px-2 py-1 text-sm bg-white"
          >
            <option value="ANY">Any</option>
            <option value="MEDIUM">Medium+</option>
            <option value="HIGH">High only</option>
          </select>
        </label>

        <button
          onClick={load}
          className="ml-auto text-xs px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {costPlaceholderWarning && (
        <div className="mb-4 border border-amber-300 bg-amber-50 rounded-lg p-3 text-sm text-amber-900">
          <div className="font-medium mb-1">Cost data incomplete</div>
          {costPlaceholderWarning}
        </div>
      )}

      {err && (
        <div className="mb-4 border border-rose-300 bg-rose-50 rounded-lg p-3 text-sm text-rose-900">
          {err}
        </div>
      )}

      {/* Summary */}
      {s && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
          <SummaryCard label="Total net revenue" value={money(s.total_net_revenue, marketplace)} />
          <SummaryCard
            label="Total net profit"
            value={money(s.total_net_profit, marketplace)}
            emphasis={s.total_net_profit >= 0 ? 'good' : 'bad'}
          />
          <BucketCard label="Healthy ≥20%" n={buckets?.healthy ?? 0} tone="good" />
          <BucketCard label="Thin 5–20%" n={buckets?.thin ?? 0} tone="warn" />
          <BucketCard label="Unprofitable" n={buckets?.unprofitable ?? 0} tone="bad" />
          <BucketCard label="Unknown" n={buckets?.unknown ?? 0} tone="mute" />
        </div>
      )}

      {/* Table */}
      <div className="bg-white rounded-lg border border-slate-200 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key as string}
                  onClick={() => headerClick(c.key)}
                  className={
                    'px-3 py-2 cursor-pointer select-none whitespace-nowrap ' +
                    (c.align === 'right' ? 'text-right ' : 'text-left ') +
                    'hover:bg-slate-100'
                  }
                >
                  {c.label}
                  {sortKey === c.key && (
                    <span className="ml-1 text-slate-400">
                      {sortDir === 'asc' ? '▲' : '▼'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr><td colSpan={columns.length} className="p-6 text-center text-slate-400">Loading…</td></tr>
            )}
            {!loading && filteredRows.length === 0 && (
              <tr><td colSpan={columns.length} className="p-6 text-center text-slate-400">No rows.</td></tr>
            )}
            {!loading && filteredRows.map((r) => (
              <tr key={`${r.asin}-${r.marketplace}`} className="hover:bg-slate-50">
                {columns.map((c) => {
                  const raw = (r as unknown as Record<string, unknown>)[c.key]
                  let cell: React.ReactNode
                  if (c.key === 'confidence') {
                    const b = confidenceBadge(r.confidence)
                    cell = (
                      <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded ${b.cls}`}>
                        {b.label}
                      </span>
                    )
                  } else if (c.key === 'net_margin_pct') {
                    cell = <span className={marginCellClass(r.net_margin_pct)}>{pct(r.net_margin_pct)}</span>
                  } else if (c.key === 'net_profit') {
                    const cls = r.net_profit === null
                      ? 'text-slate-400'
                      : (r.net_profit >= 0 ? 'text-emerald-700' : 'text-rose-700')
                    cell = <span className={cls}>{money(r.net_profit, marketplace)}</span>
                  } else if (c.format) {
                    cell = c.format(raw, r)
                  } else {
                    cell = raw === null || raw === undefined ? '—' : String(raw)
                  }
                  return (
                    <td
                      key={c.key as string}
                      className={
                        'px-3 py-2 whitespace-nowrap ' +
                        (c.align === 'right' ? 'text-right ' : 'text-left ') +
                        (r.confidence === 'LOW' ? 'text-slate-500' : 'text-slate-800')
                      }
                    >
                      {cell}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <footer className="mt-3 text-xs text-slate-500">
        {data && (
          <>
            Showing <span className="font-medium">{filteredRows.length}</span> of{' '}
            <span className="font-medium">{data.summary.total_skus}</span> SKUs
            {' · '}marketplace <span className="font-medium">{data.marketplace}</span>
            {' · '}last {data.lookback_days} days
          </>
        )}
      </footer>
    </div>
  )
}

function SummaryCard({ label, value, emphasis }: { label: string; value: string; emphasis?: 'good' | 'bad' }) {
  const valClass = emphasis === 'good'
    ? 'text-emerald-700'
    : emphasis === 'bad'
    ? 'text-rose-700'
    : 'text-slate-900'
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-lg font-semibold mt-0.5 ${valClass}`}>{value}</div>
    </div>
  )
}

function BucketCard({ label, n, tone }: { label: string; n: number; tone: 'good' | 'warn' | 'bad' | 'mute' }) {
  const cls =
    tone === 'good' ? 'text-emerald-700' :
    tone === 'warn' ? 'text-amber-700' :
    tone === 'bad'  ? 'text-rose-700'   :
                      'text-slate-500'
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-lg font-semibold mt-0.5 ${cls}`}>{n}</div>
    </div>
  )
}
