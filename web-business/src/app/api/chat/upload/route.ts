import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { CAIRN_API_URL, CAIRN_API_KEY } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

/**
 * Smart file upload — detects file type and routes to the correct handler:
 * - .csv (business report) → AMI business report parser
 * - .xlsm (flatfile) → AMI flatfile parser
 * - .xlsx (advertising) → AMI advertising parser
 * - .txt/.md → Cairn memory (document)
 * - .pdf/.docx → Cairn memory (document, text extraction placeholder)
 */

interface UploadResult {
  success: boolean
  type: string
  summary: string
  detail?: string
}

const AMI_ROUTES: Record<string, { endpoint: string; source_type: string; label: string }> = {
  '.csv': { endpoint: '/ami/upload/business-report', source_type: 'business_report', label: 'Amazon Business Report' },
  '.xlsm': { endpoint: '/ami/upload/flatfile', source_type: 'flatfile', label: 'Amazon Inventory Flatfile' },
  '.xlsx': { endpoint: '/ami/upload/advertising', source_type: 'advertising_report', label: 'Amazon Advertising Report' },
  '.tsv': { endpoint: '/ami/upload/all-listings', source_type: 'all_listings', label: 'Amazon All Listings Report' },
}

const MEMORY_EXTENSIONS = new Set(['.txt', '.md', '.pdf', '.docx', '.png', '.jpg', '.jpeg', '.webp', '.gif'])

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
    return NextResponse.json({ error: 'Invalid form data' }, { status: 400 })
  }

  const file = formData.get('file')
  if (!file || !(file instanceof Blob)) {
    return NextResponse.json({ error: 'No file provided' }, { status: 400 })
  }

  const filename = (file as File).name ?? 'upload'
  const ext = '.' + filename.split('.').pop()?.toLowerCase()

  // Route to AMI parser
  const amiRoute = AMI_ROUTES[ext]
  if (amiRoute) {
    return handleAmiUpload(file, filename, amiRoute)
  }

  // Route to memory (document)
  if (MEMORY_EXTENSIONS.has(ext)) {
    return handleMemoryUpload(file, filename, ext)
  }

  return NextResponse.json({
    success: false,
    type: 'unknown',
    summary: `Unsupported file type: ${ext}`,
    detail: 'Supported: .csv (business report), .xlsm (flatfile), .xlsx (advertising), .tsv (all listings), .txt, .md, .pdf, .docx',
  })
}

async function handleAmiUpload(
  file: Blob,
  filename: string,
  route: { endpoint: string; source_type: string; label: string },
): Promise<NextResponse> {
  const upstream = new FormData()
  upstream.append('file', file, filename)

  try {
    const res = await fetch(`${CAIRN_API_URL}${route.endpoint}`, {
      method: 'POST',
      headers: { 'X-API-Key': CAIRN_API_KEY },
      body: upstream,
    })

    if (!res.ok) {
      const errText = await res.text().catch(() => '')
      return NextResponse.json({
        success: false,
        type: route.source_type,
        summary: `Failed to process ${route.label}`,
        detail: errText.slice(0, 200),
      })
    }

    const data = await res.json()
    const rows = data.rows_parsed ?? data.rows_inserted ?? data.count ?? 0
    const skipped = data.rows_skipped ?? 0

    return NextResponse.json({
      success: true,
      type: route.source_type,
      summary: `${route.label} uploaded: ${rows} rows parsed${skipped ? `, ${skipped} skipped` : ''}`,
      detail: JSON.stringify(data),
    })
  } catch {
    return NextResponse.json({
      success: false,
      type: route.source_type,
      summary: `${route.label} upload failed — Cairn API unreachable`,
    })
  }
}

async function handleMemoryUpload(
  file: Blob,
  filename: string,
  ext: string,
): Promise<NextResponse> {
  const { extractText } = await import('@/lib/file-extract')
  const bytes = await file.arrayBuffer()
  const buffer = Buffer.from(bytes)
  const { text, method } = await extractText(buffer, filename, ext)

  const preview = text.slice(0, 500)

  try {
    const res = await fetch(`${CAIRN_API_URL}/memory/write`, {
      method: 'POST',
      headers: {
        'X-API-Key': CAIRN_API_KEY,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        project: 'nbne',
        query: `Document: ${filename}`,
        decision: text.slice(0, 5000),
        outcome: 'committed',
        model: 'upload',
        files_changed: [filename],
      }),
    })

    if (!res.ok) {
      return NextResponse.json({
        success: false,
        type: 'document',
        summary: `Failed to save ${filename} to memory`,
      })
    }

    return NextResponse.json({
      success: true,
      type: 'document',
      summary: `${filename} saved to memory (${text.length} chars)`,
      detail: preview,
    })
  } catch {
    return NextResponse.json({
      success: false,
      type: 'document',
      summary: `Failed to save ${filename} — Cairn API unreachable`,
    })
  }
}
