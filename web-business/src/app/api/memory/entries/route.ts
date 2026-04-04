import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

export async function GET(req: NextRequest) {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value

  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }
  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }

  const { searchParams } = new URL(req.url)
  const project = searchParams.get('project') ?? 'nbne'
  const limit = searchParams.get('limit') ?? '50'
  const offset = searchParams.get('offset') ?? '0'
  const q = searchParams.get('q') ?? ''

  const params = new URLSearchParams({ project, limit, offset })
  if (q) params.set('q', q)

  try {
    const cairnRes = await cairnFetch(`/memory/entries?${params.toString()}`, {
      cache: 'no-store',
    })
    if (!cairnRes.ok) {
      return NextResponse.json({ results: [], total: 0 }, { status: 200 })
    }
    const data = await cairnRes.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ results: [], total: 0 }, { status: 200 })
  }
}
