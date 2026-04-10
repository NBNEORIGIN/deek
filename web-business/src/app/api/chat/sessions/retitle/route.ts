import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

/**
 * One-off migration endpoint — recomputes session titles that were polluted
 * with context-injection blocks ([PERSONALITY] / [LIVE BUSINESS DATA] / ...).
 * Safe to call repeatedly; the underlying Cairn endpoint is idempotent.
 */
export async function POST(_req: NextRequest) {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value
  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }
  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }

  try {
    const res = await cairnFetch('/projects/nbne/sessions/retitle', {
      method: 'POST',
      cache: 'no-store',
    })
    if (!res.ok) {
      return NextResponse.json({ error: 'Migration failed' }, { status: 500 })
    }
    return NextResponse.json(await res.json())
  } catch {
    return NextResponse.json({ error: 'Cairn API unavailable' }, { status: 503 })
  }
}
