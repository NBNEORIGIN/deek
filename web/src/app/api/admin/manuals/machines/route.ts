/**
 * Proxy: GET /api/admin/manuals/machines — distinct machine names
 * (folders on disk + machines that already have indexed chunks).
 * ADMIN-only — same enforcement pattern as /api/admin/users.
 */
import { NextResponse } from 'next/server'
import { getServerSession } from '@/lib/auth'

const DEEK_API_URL =
  process.env.DEEK_API_URL ||
  process.env.CLAW_API_URL ||
  'http://localhost:8765'
const DEEK_API_KEY =
  process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  if (session.role !== 'ADMIN') {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 })
  }
  try {
    const res = await fetch(`${DEEK_API_URL}/api/deek/manuals/machines`, {
      headers: { 'X-API-Key': DEEK_API_KEY },
      cache: 'no-store',
      signal: AbortSignal.timeout(8_000),
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ machines: [] }, { status: 502 })
  }
}
