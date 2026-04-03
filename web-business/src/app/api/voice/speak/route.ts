import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

export async function POST(req: NextRequest) {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value

  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }
  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }

  const openaiKey = process.env.OPENAI_API_KEY
  if (!openaiKey) {
    return NextResponse.json({ error: 'Speech not configured' }, { status: 503 })
  }

  let body: { text?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
  }

  const text = body.text?.trim()
  if (!text) {
    return NextResponse.json({ error: 'No text provided' }, { status: 400 })
  }

  // Truncate to 4096 chars (OpenAI TTS limit)
  const truncated = text.slice(0, 4096)

  let ttsRes: Response
  try {
    ttsRes = await fetch('https://api.openai.com/v1/audio/speech', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${openaiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'tts-1',
        input: truncated,
        voice: 'nova',
        response_format: 'mp3',
      }),
    })
  } catch {
    return NextResponse.json({ error: 'Speech service unavailable' }, { status: 503 })
  }

  if (!ttsRes.ok) {
    return NextResponse.json({ error: 'Speech generation failed' }, { status: 502 })
  }

  const audioBuffer = await ttsRes.arrayBuffer()
  return new NextResponse(audioBuffer, {
    headers: {
      'Content-Type': 'audio/mpeg',
      'Cache-Control': 'no-cache',
    },
  })
}
