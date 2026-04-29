/**
 * Proxy: GET /api/voice/sessions/list
 *   → GET ${DEEK_API}/api/deek/voice/sessions/list?user=<session.email>&limit=N
 *
 * Used by the chat-history sidebar on /voice. Returns distinct past
 * sessions for the authenticated user, most recent first.
 */
import { NextRequest, NextResponse } from 'next/server'
import { getServerSession } from '@/lib/auth'

const DEEK_API_URL =
  process.env.DEEK_API_URL ||
  process.env.CLAW_API_URL ||
  'http://localhost:8765'
const DEEK_API_KEY =
  process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  const url = new URL(req.url)
  const limit = Math.min(
    Math.max(parseInt(url.searchParams.get('limit') || '30', 10) || 30, 1),
    100,
  )
  try {
    const res = await fetch(
      `${DEEK_API_URL}/api/deek/voice/sessions/list?user=${encodeURIComponent(session.email)}&limit=${limit}`,
      {
        headers: { 'X-API-Key': DEEK_API_KEY },
        cache: 'no-store',
        signal: AbortSignal.timeout(8_000),
      },
    )
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ sessions: [] }, { status: 502 })
  }
}
