'use client'

/**
 * Tiny client component for the password input — adds a show/hide
 * toggle (eye icon) without converting the whole login page back to
 * a client component (the page is a server component on purpose so
 * the form POST + 303 redirect path stays JS-free and ITP-resistant).
 */
import { useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'

export function PasswordField() {
  const [reveal, setReveal] = useState(false)
  return (
    <div className="relative">
      <input
        id="password"
        name="password"
        type={reveal ? 'text' : 'password'}
        autoComplete="current-password"
        required
        className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 pr-10 text-base text-gray-900 placeholder-gray-400 focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400"
      />
      <button
        type="button"
        onClick={() => setReveal(v => !v)}
        tabIndex={-1}
        aria-label={reveal ? 'Hide password' : 'Show password'}
        title={reveal ? 'Hide password' : 'Show password'}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
      >
        {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  )
}
