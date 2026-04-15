import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'

// Proxy for /ami/margin/* and /ami/fees/* endpoints.
// Usage: /api/margin?path=/per-sku&marketplace=UK&lookback_days=30

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url)
  const path = searchParams.get('path') ?? '/per-sku'

  const forward = new URLSearchParams()
  searchParams.forEach((v, k) => {
    if (k !== 'path') forward.append(k, v)
  })

  const qs = forward.toString()
  const upstream = `/ami/margin${path}${qs ? `?${qs}` : ''}`

  try {
    const res = await cairnFetch(upstream, { cache: 'no-store' })
    const data = await res.json()
    if (!res.ok) {
      return NextResponse.json(data, { status: res.status })
    }
    return NextResponse.json(data)
  } catch (err) {
    return NextResponse.json({ error: 'Margin service unavailable' }, { status: 503 })
  }
}
