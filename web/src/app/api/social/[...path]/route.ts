/**
 * Catch-all proxy for /api/social/* → backend /social/*
 *
 * The social page (web/src/app/social/page.tsx) used to call
 * http://localhost:8765/social/* directly from the browser, which
 * breaks in Docker (the browser can't reach deek-api:8765).
 *
 * This proxy forwards GET and POST requests through the Next.js
 * server, which CAN reach deek-api inside the Docker network.
 */

import { NextRequest, NextResponse } from 'next/server'

const CLAW_API = process.env.CLAW_API_URL || process.env.DEEK_API_URL || 'http://localhost:8765'
// NEVER fall back to the placeholder literal — SWC folds it into the
// compiled bundle at build time and ships it to production. See
// docs/audit/IDENTITY_ISOLATION_AUDIT_2026-04.md audit finding F3 and
// recommendation R4. Empty string is honest; placeholder is not.
const CLAW_KEY = process.env.DEEK_API_KEY || process.env.CLAW_API_KEY || ''

async function proxyToBackend(
  request: NextRequest,
  params: { path: string[] },
) {
  const backendPath = params.path.join('/')
  const url = new URL(`${CLAW_API}/social/${backendPath}`)

  // Forward query params
  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value)
  })

  const headers: Record<string, string> = {
    'X-API-Key': CLAW_KEY,
  }

  let body: string | undefined
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    const contentType = request.headers.get('content-type')
    if (contentType) headers['Content-Type'] = contentType
    body = await request.text()
  }

  try {
    const upstream = await fetch(url.toString(), {
      method: request.method,
      headers,
      body,
    })

    const data = await upstream.text()
    return new NextResponse(data, {
      status: upstream.status,
      headers: {
        'Content-Type': upstream.headers.get('content-type') || 'application/json',
      },
    })
  } catch (err) {
    return NextResponse.json(
      { error: `Backend unreachable: ${(err as Error).message}` },
      { status: 503 },
    )
  }
}

export async function GET(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyToBackend(request, params)
}

export async function POST(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyToBackend(request, params)
}

export async function PUT(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyToBackend(request, params)
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: { path: string[] } },
) {
  return proxyToBackend(request, params)
}
