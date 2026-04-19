/**
 * Proxy: GET /api/voice/briefing/morning → GET /api/deek/briefing/morning
 *
 * Session-cookie auth on the public side; injects DEEK_API_KEY for
 * the backend call.
 */
import { NextRequest, NextResponse } from 'next/server'
import { getServerSession } from '@/lib/auth'

function deekConfig() {
  return {
    apiUrl:
      process.env.DEEK_API_URL ||
      process.env.CLAW_API_URL ||
      'http://localhost:8765',
    apiKey:
      process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || '',
  }
}

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export async function GET(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  const cfg = deekConfig()
  const limit = req.nextUrl.searchParams.get('limit') || '5'
  try {
    const r = await fetch(
      `${cfg.apiUrl}/api/deek/briefing/morning?limit=${encodeURIComponent(limit)}`,
      {
        method: 'GET',
        headers: { 'X-API-Key': cfg.apiKey, Accept: 'application/json' },
      },
    )
    const body = await r.text()
    return new Response(body, {
      status: r.status,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err: any) {
    return NextResponse.json(
      { error: 'upstream_failed', detail: String(err?.message || err) },
      { status: 502 },
    )
  }
}
