/**
 * Proxy: DELETE /api/admin/manuals/by-path?file_path=X — remove one
 * manual's chunks AND the file from disk. ADMIN-only.
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

export async function DELETE(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  if (session.role !== 'ADMIN') {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 })
  }
  const fp = req.nextUrl.searchParams.get('file_path') || ''
  if (!fp) {
    return NextResponse.json({ error: 'file_path required' }, { status: 400 })
  }

  try {
    const upstream = await fetch(
      `${DEEK_API_URL}/api/deek/manuals/by-path?file_path=${encodeURIComponent(fp)}`,
      {
        method: 'DELETE',
        headers: { 'X-API-Key': DEEK_API_KEY },
        signal: AbortSignal.timeout(8_000),
      },
    )
    const data = await upstream.json().catch(() => ({}))
    return NextResponse.json(data, { status: upstream.status })
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || 'upstream failed' },
      { status: 502 },
    )
  }
}
