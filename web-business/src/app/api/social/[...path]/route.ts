import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

/**
 * Auth-guarded catch-all proxy to the Cairn Social module.
 *
 * The browser calls /api/social/<anything>, this route checks the Next
 * auth cookie, then forwards to the Cairn API with the server-side
 * X-API-Key via cairnFetch. Keeps the Cairn key out of the browser and
 * means adding new social endpoints on the backend requires no web
 * changes — they're reachable immediately.
 */

async function guard(): Promise<NextResponse | null> {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value
  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }
  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }
  return null
}

function buildCairnPath(req: NextRequest, segments: string[]): string {
  const search = req.nextUrl.search ?? ''
  const suffix = segments.length ? '/' + segments.join('/') : ''
  return `/social${suffix}${search}`
}

async function forward(
  req: NextRequest,
  segments: string[],
  method: 'GET' | 'POST',
): Promise<NextResponse> {
  const blocked = await guard()
  if (blocked) return blocked

  const path = buildCairnPath(req, segments)
  const init: RequestInit = { method }

  if (method === 'POST') {
    let body: unknown = {}
    try {
      const text = await req.text()
      body = text ? JSON.parse(text) : {}
    } catch {
      return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
    }
    init.headers = { 'Content-Type': 'application/json' }
    init.body = JSON.stringify(body)
  }

  try {
    const res = await cairnFetch(path, init)
    const text = await res.text()
    const contentType = res.headers.get('content-type') ?? 'application/json'
    return new NextResponse(text, {
      status: res.status,
      headers: { 'content-type': contentType },
    })
  } catch {
    return NextResponse.json(
      { error: 'Cairn social service unavailable' },
      { status: 503 },
    )
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params
  return forward(req, path ?? [], 'GET')
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params
  return forward(req, path ?? [], 'POST')
}
