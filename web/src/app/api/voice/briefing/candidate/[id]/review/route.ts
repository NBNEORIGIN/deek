/**
 * Proxy: POST /api/voice/briefing/candidate/{id}/review
 *       → POST /api/deek/briefing/candidate/{id}/review
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

export async function POST(
  req: NextRequest,
  { params }: { params: { id: string } },
) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  const cfg = deekConfig()
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
  }
  try {
    const r = await fetch(
      `${cfg.apiUrl}/api/deek/briefing/candidate/${encodeURIComponent(params.id)}/review`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': cfg.apiKey,
        },
        body: JSON.stringify(body),
      },
    )
    const text = await r.text()
    return new Response(text, {
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
