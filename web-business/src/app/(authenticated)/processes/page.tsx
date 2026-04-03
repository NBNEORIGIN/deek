'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'

interface Process {
  id: string
  title: string
  summary: string
  content?: string
}

const PROCESSES: Process[] = [
  {
    id: '001001',
    title: 'How to Calculate Master Stock',
    summary: 'Pull sales data from all channels, build pivot by M-number for daily velocity and restock levels',
  },
  {
    id: '001003',
    title: 'How to Manage D2C Orders',
    summary: 'Daily order workflow across Amazon, Etsy, eBay, Shopify via Zen Stores',
  },
  {
    id: '001004',
    title: 'How to Design & Manufacture Personalised Memorials',
    summary: 'Inkscape design, sublimation print, heat press process',
  },
  {
    id: '001005',
    title: 'How to Use the Heat Press',
    summary: 'Temperature and time settings for memorials and tee-shirts',
  },
  {
    id: '001006',
    title: 'How to Create an MCF Order',
    summary: 'Amazon multi-channel fulfilment for non-Amazon orders',
  },
  {
    id: '001007',
    title: 'How to Download a Canva Graphic as SVG',
    summary: 'Export graphics from Canva for use in Inkscape',
  },
  {
    id: '001008',
    title: 'How to Calculate AMZ Restock Requirements',
    summary: 'Pull restock report, filter SKUs, calculate 30-60 day coverage',
  },
  {
    id: '001009',
    title: 'How to Book an AMZ Shipment UK',
    summary: 'Upload manifest, add packing info, book UPS collection',
  },
]

export default function ProcessesPage() {
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const router = useRouter()

  const filtered = PROCESSES.filter(
    (p) =>
      p.title.toLowerCase().includes(search.toLowerCase()) ||
      p.summary.toLowerCase().includes(search.toLowerCase())
  )

  function handleAskAbout(title: string) {
    router.push(`/ask?q=${encodeURIComponent(`Tell me about: ${title}`)}`)
  }

  function toggleExpand(id: string) {
    setExpanded((prev) => (prev === id ? null : id))
  }

  return (
    <div className="max-w-5xl space-y-4 md:space-y-6">
      {/* Search */}
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 text-base pointer-events-none">
          🔍
        </span>
        <input
          type="search"
          placeholder="Search processes…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full pl-9 pr-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
        />
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="bg-white rounded-xl border border-slate-200 px-5 py-10 text-center">
          <p className="text-sm text-slate-400">No processes match your search.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-4">
          {filtered.map((proc) => {
            const isOpen = expanded === proc.id
            return (
              <div
                key={proc.id}
                className="bg-white rounded-xl border border-slate-200 overflow-hidden"
              >
                {/* Card header */}
                <button
                  className="w-full text-left px-4 py-4 md:px-5 flex items-start gap-3 hover:bg-slate-50 transition-colors min-h-[44px]"
                  onClick={() => toggleExpand(proc.id)}
                >
                  <div className="flex-shrink-0 mt-0.5">
                    <span className="inline-block text-xs font-medium bg-slate-100 text-slate-500 px-2 py-0.5 rounded">
                      {proc.id}
                    </span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-800 leading-snug">{proc.title}</p>
                    <p className="text-xs text-slate-500 mt-1">{proc.summary}</p>
                  </div>
                  <span className="flex-shrink-0 text-slate-400 text-xs mt-0.5">
                    {isOpen ? '▲' : '▼'}
                  </span>
                </button>

                {/* Expanded content */}
                {isOpen && (
                  <div className="px-5 pb-5 border-t border-slate-100">
                    <div className="pt-4 text-sm text-slate-600 leading-relaxed">
                      {proc.content ? (
                        <p>{proc.content}</p>
                      ) : (
                        <p className="text-slate-400 italic">
                          Full process documentation coming soon. Use the button below to ask about this process.
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => handleAskAbout(proc.title)}
                      className="mt-4 text-sm text-indigo-600 hover:text-indigo-700 font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <span>💬</span>
                      Ask about this
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
