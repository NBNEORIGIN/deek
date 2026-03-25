import { NextResponse } from 'next/server'

const CLAW_API = process.env.CLAW_API_URL || 'http://localhost:8765'
const CLAW_KEY = process.env.CLAW_API_KEY || ''

export async function GET() {
  try {
    const r = await fetch(`${CLAW_API}/status/summary`, {
      headers: { 'X-API-Key': CLAW_KEY },
      signal: AbortSignal.timeout(5000),
      cache: 'no-store',
    })
    if (!r.ok) {
      return NextResponse.json({ error: `API returned ${r.status}` }, { status: r.status })
    }
    const data = await r.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'API offline' }, { status: 503 })
  }
}
