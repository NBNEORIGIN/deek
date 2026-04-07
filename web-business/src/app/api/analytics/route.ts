import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'

// Proxy for all /ami/analytics/* endpoints
// Usage: /api/analytics?path=/revenue/summary&marketplace=GB

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const path = searchParams.get('path') ?? '/revenue/summary'

  // Forward remaining query params (everything except 'path')
  const forward = new URLSearchParams()
  searchParams.forEach((v, k) => {
    if (k !== 'path') forward.append(k, v)
  })

  const qs = forward.toString()
  const upstream = `/ami/analytics${path}${qs ? `?${qs}` : ''}`

  try {
    const res = await cairnFetch(upstream, { cache: 'no-store' })
    const data = await res.json()
    if (!res.ok) {
      return NextResponse.json(data, { status: res.status })
    }
    return NextResponse.json(data)
  } catch (err) {
    return NextResponse.json({ error: 'Analytics unavailable' }, { status: 503 })
  }
}

export async function POST(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const path = searchParams.get('path') ?? ''
  const body = await req.json().catch(() => ({}))

  const upstream = `/ami/analytics${path}`

  try {
    const res = await cairnFetch(upstream, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch (err) {
    return NextResponse.json({ error: 'Analytics unavailable' }, { status: 503 })
  }
}
