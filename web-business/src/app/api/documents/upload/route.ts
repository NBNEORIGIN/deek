import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'
import { extractText } from '@/lib/file-extract'

const MAX_DECISION_CHARS = 5000

function getExtension(filename: string): string {
  const dot = filename.lastIndexOf('.')
  return dot >= 0 ? filename.slice(dot).toLowerCase() : ''
}

export async function POST(req: NextRequest) {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value

  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }
  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }

  let formData: FormData
  try {
    formData = await req.formData()
  } catch {
    return NextResponse.json({ error: 'Invalid request body' }, { status: 400 })
  }

  const file = formData.get('file')
  if (!file || !(file instanceof File)) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 })
  }

  const filename = file.name
  const ext = getExtension(filename)

  // Extract text using shared extraction library
  const bytes = await file.arrayBuffer()
  const buffer = Buffer.from(bytes)
  const { text: extractedText, method } = await extractText(buffer, filename, ext)

  const decision = extractedText.slice(0, MAX_DECISION_CHARS)
  const preview = extractedText.slice(0, 500)

  // Write to Cairn memory (now also embeds to pgvector via updated /memory/write)
  try {
    const cairnRes = await cairnFetch('/memory/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project: 'nbne',
        query: `Document: ${filename}`,
        decision,
        outcome: 'committed',
        model: `upload:${method}`,
        files_changed: [filename],
      }),
    })

    if (!cairnRes.ok) {
      return NextResponse.json({ error: 'Failed to save to memory' }, { status: 502 })
    }
  } catch {
    return NextResponse.json({ error: 'Memory service unavailable' }, { status: 503 })
  }

  return NextResponse.json({ success: true, preview, method })
}
