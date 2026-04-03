'use client'

import { useAuth } from '@/components/auth/AuthProvider'
import { useRouter } from 'next/navigation'

export interface HeaderProps {
  title: string
  onMenuToggle: () => void
}

const ROLE_BADGE: Record<string, { label: string; className: string }> = {
  staff: {
    label: 'Staff',
    className: 'bg-blue-100 text-blue-700',
  },
  manager: {
    label: 'Manager',
    className: 'bg-amber-100 text-amber-700',
  },
  owner: {
    label: 'Owner',
    className: 'bg-indigo-100 text-indigo-700',
  },
}

export default function Header({ title, onMenuToggle }: HeaderProps) {
  const { user, logout } = useAuth()
  const router = useRouter()

  const badge = user?.role ? (ROLE_BADGE[user.role] ?? ROLE_BADGE.staff) : ROLE_BADGE.staff
  const firstName = user?.first_name ?? user?.username ?? ''

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' })
    logout()
    router.push('/login')
  }

  return (
    <header className="h-14 bg-white border-b border-slate-200 flex items-center justify-between px-4 md:px-6 fixed top-0 left-0 right-0 md:left-[240px] z-10">
      {/* Left: hamburger (mobile only) + title */}
      <div className="flex items-center gap-3">
        {/* Hamburger — mobile only */}
        <button
          onClick={onMenuToggle}
          className="md:hidden flex items-center justify-center w-10 h-10 rounded-lg text-slate-600 hover:bg-slate-100 transition-colors flex-shrink-0"
          aria-label="Open menu"
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
            <rect y="3" width="20" height="2" rx="1" />
            <rect y="9" width="20" height="2" rx="1" />
            <rect y="15" width="20" height="2" rx="1" />
          </svg>
        </button>

        <h1 className="text-base font-semibold text-slate-800">{title}</h1>
      </div>

      {/* Right: user info + sign out */}
      <div className="flex items-center gap-2 md:gap-3">
        {/* Name — hidden on very small screens */}
        {firstName && (
          <span className="hidden sm:block text-sm text-slate-700 font-medium">{firstName}</span>
        )}
        {/* Role badge — hidden on very small screens */}
        {user?.role && (
          <span
            className={`hidden sm:block text-xs font-medium px-2 py-0.5 rounded-full ${badge.className}`}
          >
            {badge.label}
          </span>
        )}
        {/* Sign out — text hidden on mobile, icon always shown */}
        <button
          onClick={handleLogout}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors px-2 py-1.5 rounded hover:bg-slate-100 min-h-[44px]"
          aria-label="Sign out"
        >
          {/* Exit icon */}
          <svg
            className="w-4 h-4 flex-shrink-0"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
            />
          </svg>
          <span className="hidden md:block">Sign out</span>
        </button>
      </div>
    </header>
  )
}
