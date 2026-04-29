'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { MessageSquare, Share2, Activity, Mic } from 'lucide-react'
import { BRAND } from '@/lib/brand'

/**
 * `/voice` is the primary mobile entry point — labelled with the brand
 * name. The legacy `/` (ChatWindow + sidebar) is the desktop power-user
 * console, labelled "Console". Mobile users are redirected from / to
 * /voice by middleware, so on phones they'll almost never see the
 * Console tab highlighted.
 *
 * Social and Status are secondary — hidden behind a small collapse on mobile
 * so the primary two tabs get the space.
 */
const PRIMARY_ITEMS = [
  { href: '/voice', label: BRAND, icon: Mic },
  { href: '/', label: 'Console', icon: MessageSquare, desktopOnly: true },
]

const SECONDARY_ITEMS = [
  { href: '/social', label: 'Social', icon: Share2 },
  { href: '/status', label: 'Status', icon: Activity },
]

export function NavBar() {
  const pathname = usePathname()

  // Voice pages (+ login) and admin pages use their own dark chrome and
  // in-page mode switcher — hide the global NavBar entirely so the PWA
  // feels like a native app.
  if (
    pathname === '/voice' ||
    pathname?.startsWith('/voice/') ||
    pathname?.startsWith('/admin/')
  ) {
    return null
  }

  return (
    <nav className="flex flex-shrink-0 items-center gap-0.5 border-b border-slate-200 bg-white px-3 py-1">
      {PRIMARY_ITEMS.map(item => {
        const active = pathname === item.href
        const Icon = item.icon
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              item.desktopOnly ? 'hidden md:inline-flex' : ''
            } ${
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
      <div className="ml-auto flex items-center gap-0.5">
        {SECONDARY_ITEMS.map(item => {
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
              <span className="hidden sm:inline">{item.label}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
