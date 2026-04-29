/**
 * Proxy: PATCH /api/voice/sessions/{id}
 *   → PATCH ${DEEK_API}/api/deek/voice/sessions/{id}
 *
 * Body shape:
 *   { title?: string, project_id?: number | null, archived?: boolean }
 *
 * All three fields are optional — pass only what's changing. Pass
 * project_id: null to ungroup.
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

export async function PATCH(
  req: NextRequest,
  { params }: { params: { id: string } },
) {
  const session = await getServerSession()
  if (!session) return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })

  const sessionId = params?.id
  if (!sessionId) return NextResponse.json({ error: 'session_id_required' }, { status: 400 })

  let body: any
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }

  // Forward only the meta fields, plus the user we read from the cookie.
  const upstreamBody: any = { user: session.email }
  if ('title' in body) upstreamBody.title = body.title
  if ('project_id' in body) upstreamBody.project_id = body.project_id
  if ('archived' in body) upstreamBody.archived = body.archived

  try {
    const res = await fetch(
      `${DEEK_API_URL}/api/deek/voice/sessions/${encodeURIComponent(sessionId)}`,
      {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': DEEK_API_KEY,
        },
        body: JSON.stringify(upstreamBody),
        cache: 'no-store',
        signal: AbortSignal.timeout(10_000),
      },
    )
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_failed' }, { status: 502 })
  }
}
