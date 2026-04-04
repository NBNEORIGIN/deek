'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect } from 'react'

const PRIMARY_NAV = [
  { label: 'Priorities', href: '/dashboard', icon: '📋' },
  { label: 'Ask', href: '/ask', icon: '💬' },
  { label: 'How We Do Things', href: '/processes', icon: '📖' },
]

const SECONDARY_NAV = [
  { label: 'Voice Memos', href: '/voice', icon: '🎙️' },
  { label: 'Documents', href: '/documents', icon: '📄' },
  { label: 'Notes', href: '/notes', icon: '📝' },
  { label: 'Memory', href: '/memory', icon: '🧠' },
]

export interface SidebarProps {
  isOpen: boolean
  onClose: () => void
}

function NavItem({
  item,
  isActive,
  onClose,
}: {
  item: { label: string; href: string; icon: string }
  isActive: boolean
  onClose: () => void
}) {
  return (
    <li>
      <Link
        href={item.href}
        onClick={onClose}
        className={
          'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors min-h-[44px] ' +
          (isActive
            ? 'bg-indigo-50 text-indigo-700'
            : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900')
        }
      >
        <span className="text-base leading-none">{item.icon}</span>
        <span>{item.label}</span>
      </Link>
    </li>
  )
}

function SidebarContent({
  pathname,
  onClose,
}: {
  pathname: string
  onClose: () => void
}) {
  return (
    <>
      {/* Logo */}
      <div className="h-14 flex items-center px-6 border-b border-slate-200">
        <span className="text-xl font-bold text-slate-900 tracking-tight">NBNE</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-3">
        <ul className="space-y-1">
          {PRIMARY_NAV.map((item) => (
            <NavItem
              key={item.href}
              item={item}
              isActive={pathname === item.href || pathname.startsWith(item.href + '/')}
              onClose={onClose}
            />
          ))}
        </ul>

        <div className="my-3 border-t border-slate-100" />

        <ul className="space-y-1">
          {SECONDARY_NAV.map((item) => (
            <NavItem
              key={item.href}
              item={item}
              isActive={pathname === item.href || pathname.startsWith(item.href + '/')}
              onClose={onClose}
            />
          ))}
        </ul>
      </nav>
    </>
  )
}

export default function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname()

  // Lock body scroll when mobile menu is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [isOpen])

  return (
    <>
      {/* Desktop sidebar — always visible on md+ */}
      <aside
        className="hidden md:flex fixed top-0 left-0 h-full bg-white border-r border-slate-200 flex-col z-20"
        style={{ width: 240 }}
      >
        <SidebarContent pathname={pathname} onClose={() => {}} />
      </aside>

      {/* Mobile overlay backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      {/* Mobile slide-in sidebar */}
      <aside
        className={
          'fixed top-0 left-0 h-full bg-white border-r border-slate-200 flex flex-col z-40 md:hidden transition-transform duration-300 ease-in-out ' +
          (isOpen ? 'translate-x-0' : '-translate-x-full')
        }
        style={{ width: 240 }}
        aria-hidden={!isOpen}
      >
        <SidebarContent pathname={pathname} onClose={onClose} />
      </aside>
    </>
  )
}
