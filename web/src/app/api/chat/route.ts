import { NextRequest, NextResponse } from 'next/server'

const CLAW_API_URL = process.env.CLAW_API_URL || 'http://localhost:8765'
const CLAW_API_KEY = process.env.CLAW_API_KEY || ''

export async function POST(req: NextRequest) {
  try {
    const body = await req.json()

    const res = await fetch(`${CLAW_API_URL}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': CLAW_API_KEY,
      },
      body: JSON.stringify(body),
    })

    const data = await res.json()
    return NextResponse.json(data)
  } catch (err) {
    return NextResponse.json(
      { error: `Cannot reach CLAW API: ${err}` },
      { status: 502 }
    )
  }
}

export async function GET() {
  try {
    const res = await fetch(`${CLAW_API_URL}/projects`, {
      headers: { 'X-API-Key': CLAW_API_KEY },
    })
    const data = await res.json()
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ projects: [] })
  }
}
