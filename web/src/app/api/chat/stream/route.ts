/**
 * Next.js SSE proxy for CLAW's /chat/stream endpoint.
 *
 * Forwards GET requests (with query params) to the FastAPI backend,
 * adding the X-API-Key header so the browser never needs to hold the key.
 * Streams the response body directly — no buffering.
 */

const CLAW_API = process.env.CLAW_API_URL || 'http://localhost:8765'
const CLAW_API_KEY = process.env.CLAW_API_KEY || 'claw-dev-key-change-in-production'

export const dynamic = 'force-dynamic'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)

  // Forward all query params to the backend
  const upstream = `${CLAW_API}/chat/stream?${searchParams.toString()}`

  let backendRes: Response
  try {
    backendRes = await fetch(upstream, {
      headers: {
        Accept: 'text/event-stream',
        'X-API-Key': CLAW_API_KEY,
        'Cache-Control': 'no-cache',
      },
      // @ts-expect-error — Next.js fetch supports duplex for streaming
      duplex: 'half',
    })
  } catch (err) {
    return new Response(
      `data: {"type":"error","message":"Cannot reach CLAW API: ${err}"}\n\ndata: {"type":"done"}\n\n`,
      {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      },
    )
  }

  if (!backendRes.ok) {
    const msg = `Backend returned ${backendRes.status}`
    return new Response(
      `data: {"type":"error","message":"${msg}"}\n\ndata: {"type":"done"}\n\n`,
      {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      },
    )
  }

  return new Response(backendRes.body, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'X-Accel-Buffering': 'no',
      Connection: 'keep-alive',
    },
  })
}
