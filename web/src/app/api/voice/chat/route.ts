import { NextRequest, NextResponse } from 'next/server'
import {
  getServerSession,
  canAccessLocation,
  locationDenyReason,
} from '@/lib/auth'

const DEEK_API_URL =
  process.env.DEEK_API_URL ||
  process.env.CLAW_API_URL ||
  'http://localhost:8765'
const DEEK_API_KEY =
  process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

export async function POST(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json(
      { error: 'not_authenticated' },
      { status: 401 },
    )
  }

  let body: any
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }

  const location = (body?.location as string) || ''
  const deny = locationDenyReason(session, location)
  if (deny) {
    return NextResponse.json(
      {
        error: 'forbidden',
        reason: deny,
        response: deny,
        outcome: 'forbidden',
        model_used: '',
        cost_usd: 0,
        latency_ms: 0,
      },
      { status: 403 },
    )
  }

  // Attach the user label so the backend can log it in telemetry.
  const payload = {
    ...body,
    user: session.email,
  }

  try {
    const res = await fetch(`${DEEK_API_URL}/api/deek/chat/voice`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': DEEK_API_KEY,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(60_000),
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch (err) {
    return NextResponse.json(
      {
        response:
          'Deek is offline. Check your connection and try again in a moment.',
        model_used: '',
        cost_usd: 0,
        latency_ms: 0,
        outcome: 'backend_error',
      },
      { status: 502 },
    )
  }
}
