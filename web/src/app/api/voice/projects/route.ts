/**
 * Proxy: POST /api/voice/projects — create
 *        DELETE /api/voice/projects?id=N — delete (with id in query)
 *
 * Listing is folded into /api/voice/sessions/list — no separate GET
 * needed here. Auth-gated by JWT cookie; user comes from session.email.
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

export async function POST(req: NextRequest) {
  const session = await getServerSession()
  if (!session) return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })

  let body: any
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }
  const name = String(body?.name || '').trim()
  if (!name) return NextResponse.json({ error: 'name_required' }, { status: 400 })

  try {
    const res = await fetch(`${DEEK_API_URL}/api/deek/voice/projects`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': DEEK_API_KEY,
      },
      body: JSON.stringify({ user: session.email, name }),
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'upstream_failed' }, { status: 502 })
  }
}

export async function DELETE(req: NextRequest) {
  const session = await getServerSession()
  if (!session) return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })

  const id = new URL(req.url).searchParams.get('id')
  if (!id) return NextResponse.json({ error: 'id_required' }, { status: 400 })

  try {
    const res = await fetch(
      `${DEEK_API_URL}/api/deek/voice/projects/${encodeURIComponent(id)}?user=${encodeURIComponent(session.email)}`,
      {
        method: 'DELETE',
        headers: { 'X-API-Key': DEEK_API_KEY },
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
