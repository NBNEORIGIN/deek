/**
 * Proxy for GET /api/deek/voice/sessions — hydrates the PWA transcript
 * on mount so conversation survives tab close / device switch.
 *
 * If ?session_id is provided, returns that conversation.
 * Otherwise uses the authenticated user's email as the user filter
 * (10-minute cross-device window, per the backend).
 */
import { NextRequest, NextResponse } from 'next/server'
import { getServerSession } from '@/lib/auth'

const DEEK_API_URL =
  process.env.DEEK_API_URL ||
  process.env.CLAW_API_URL ||
  'http://localhost:8765'
const DEEK_API_KEY =
  process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

export async function GET(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json(
      { error: 'not_authenticated' },
      { status: 401 },
    )
  }

  const { searchParams } = new URL(req.url)
  const sessionId = searchParams.get('session_id')
  const limit = searchParams.get('limit') || '20'

  const params = new URLSearchParams({ limit })
  if (sessionId) {
    params.set('session_id', sessionId)
  } else {
    // Fallback to cross-device continuity by user
    params.set('user', session.email)
  }

  try {
    const res = await fetch(
      `${DEEK_API_URL}/api/deek/voice/sessions?${params.toString()}`,
      {
        headers: { 'X-API-Key': DEEK_API_KEY },
        signal: AbortSignal.timeout(5_000),
        cache: 'no-store',
      },
    )
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch (err) {
    return NextResponse.json({ turns: [] }, { status: 502 })
  }
}
