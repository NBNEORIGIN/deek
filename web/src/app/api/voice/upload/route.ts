/**
 * Proxy: POST /api/voice/upload
 *   → POST ${DEEK_API}/api/deek/voice/upload (multipart)
 *
 * Forwards the FormData body unchanged. Auth-gated by JWT cookie like
 * every other /api/voice/* route.
 *
 * The agent stream is unchanged — the client extracts the response,
 * prepends the file text to the chat message, then calls
 * /api/voice/chat/agent-stream as normal.
 */
import { NextRequest, NextResponse } from 'next/server'
import { getServerSession } from '@/lib/auth'

const DEEK_API_URL =
  process.env.DEEK_API_URL ||
  process.env.CLAW_API_URL ||
  'http://localhost:8765'
const DEEK_API_KEY =
  process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'
export const maxDuration = 60

export async function POST(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }

  // Re-stream the multipart body to the upstream as-is. Re-building
  // FormData from the parsed payload preserves field names and content
  // types without us having to touch each part.
  let formData: FormData
  try {
    formData = await req.formData()
  } catch {
    return NextResponse.json({ error: 'invalid_form_data' }, { status: 400 })
  }

  try {
    const upstream = await fetch(`${DEEK_API_URL}/api/deek/voice/upload`, {
      method: 'POST',
      headers: {
        'X-API-Key': DEEK_API_KEY,
        // DON'T set Content-Type — let fetch generate the multipart boundary
      },
      body: formData,
      cache: 'no-store',
      signal: AbortSignal.timeout(45_000),
    })
    const data = await upstream.json()
    return NextResponse.json(data, { status: upstream.status })
  } catch (err: any) {
    return NextResponse.json(
      { error: 'upload_failed', detail: err?.message || String(err) },
      { status: 502 },
    )
  }
}
