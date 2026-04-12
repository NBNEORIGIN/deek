'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { MessageSquare, Share2, Activity, BarChart3, ShoppingBag } from 'lucide-react'

const NAV_ITEMS = [
  { href: '/', label: 'Chat', icon: MessageSquare },
  { href: '/social', label: 'Social', icon: Share2 },
  { href: '/status', label: 'Status', icon: Activity },
]

export function NavBar() {
  const pathname = usePathname()

  return (
    <nav className="flex flex-shrink-0 items-center gap-0.5 border-b border-slate-200 bg-white px-3 py-1">
      {NAV_ITEMS.map(item => {
        const active = pathname === item.href
        const Icon = item.icon
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              active
                ? 'bg-slate-100 text-slate-900'
                : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
            }`}
          >
            <Icon size={14} />
            {item.label}
          </Link>
        )
      })}
    </nav>
  )
}
