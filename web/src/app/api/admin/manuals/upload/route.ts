/**
 * Proxy: POST /api/admin/manuals/upload — multipart forward to backend.
 *
 * ADMIN-only. Forwards the entire multipart body unchanged to the
 * backend, which saves the file to /opt/nbne/manuals/<machine>/ and
 * runs the ingest pipeline inline. Big file timeout (5 min) because
 * image OCR via Claude vision can take ~10–30s per page on a
 * scanned manual.
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
// Generous: a multi-page PDF with vision OCR can run a couple of
// minutes; the embed step adds a few seconds per chunk. Avoid the
// default 30s edge cap which would kill big files mid-ingest.
export const maxDuration = 300

export async function POST(req: NextRequest) {
  const session = await getServerSession()
  if (!session) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 })
  }
  if (session.role !== 'ADMIN') {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 })
  }

  // Forward the multipart body straight through. Reading + re-building
  // FormData would buffer the whole file in Node memory; piping the
  // raw body avoids that for large PDFs.
  const contentType = req.headers.get('content-type') || ''
  if (!contentType.toLowerCase().startsWith('multipart/form-data')) {
    return NextResponse.json(
      { error: 'expected multipart/form-data' },
      { status: 400 },
    )
  }

  try {
    const upstream = await fetch(`${DEEK_API_URL}/api/deek/manuals/upload`, {
      method: 'POST',
      headers: {
        'X-API-Key': DEEK_API_KEY,
        'Content-Type': contentType,
      },
      body: req.body,
      // @ts-expect-error — Node fetch needs duplex:'half' for streaming
      // bodies; types lag the runtime.
      duplex: 'half',
      signal: AbortSignal.timeout(280_000),
    })
    const data = await upstream.json().catch(() => ({}))
    return NextResponse.json(data, { status: upstream.status })
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message || 'upstream failed' },
      { status: 502 },
    )
  }
}
