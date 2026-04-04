import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { CAIRN_API_URL, CAIRN_API_KEY } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

export const dynamic = 'force-dynamic'

/**
 * Module endpoints to fetch and inject as context into every business brain query.
 * Each module's context is fetched in parallel (2s timeout) and prepended to the
 * user's message so the agent has live data to answer business questions.
 */
// URLs use host.docker.internal to reach services on the Docker host
const DOCKER_HOST = process.env.DOCKER_HOST_GATEWAY ?? 'host.docker.internal'

const MODULE_ENDPOINTS = [
  { key: 'finance', url: 'http://ledger-backend-1:8001/api/cairn/context', label: 'Finance (Ledger)' },
  { key: 'amazon', url: `${CAIRN_API_URL}/ami/cairn/context`, label: 'Amazon Intelligence' },
]

async function fetchModuleContext(): Promise<string> {
  const results = await Promise.allSettled(
    MODULE_ENDPOINTS.map(async (mod) => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 2000)
      try {
        const res = await fetch(mod.url, {
          signal: controller.signal,
          cache: 'no-store',
          headers: {
            'X-API-Key': CAIRN_API_KEY,
            'Authorization': `Bearer ${CAIRN_API_KEY}`,
          },
        })
        clearTimeout(timer)
        if (!res.ok) return null
        const data = await res.json()
        return { key: mod.key, label: mod.label, data }
      } catch {
        clearTimeout(timer)
        return null
      }
    })
  )

  const sections: string[] = []
  for (const result of results) {
    if (result.status === 'fulfilled' && result.value) {
      const { label, data } = result.value
      // Extract the summary and key figures
      const summary = data.summary_text ?? data.summary ?? ''
      sections.push(`[${label}]: ${typeof summary === 'string' ? summary : JSON.stringify(summary)}`)

      // For finance, include detailed breakdown
      if (data.cash_position) {
        const cp = data.cash_position
        const accounts = (cp.accounts ?? [])
          .map((a: { account: string; balance: number }) => `${a.account}: £${a.balance?.toLocaleString() ?? '?'}`)
          .join(', ')
        sections.push(`  Cash: £${cp.current_balance?.toLocaleString() ?? '?'} (${accounts})`)
      }
      if (data.revenue) {
        sections.push(`  Revenue MTD: £${data.revenue.mtd?.toLocaleString() ?? '0'}, YTD: £${data.revenue.ytd?.toLocaleString() ?? '0'}`)
      }
      if (data.expenditure) {
        sections.push(`  Expenditure MTD: £${data.expenditure.mtd?.toLocaleString() ?? '0'}`)
      }
    }
  }

  if (sections.length === 0) return ''
  return '\n\n[LIVE BUSINESS DATA — use this to answer the question]\n' + sections.join('\n') + '\n[END LIVE DATA]\n\n'
}

const CAIRN_PERSONALITY = `[PERSONALITY]
You are the NBNE Business Brain. You answer questions about the business
with dry, deadpan humour — like TARS from Interstellar. Set your humour
to about 75%.

Rules:
- Be genuinely helpful and accurate first. The humour is seasoning, not the meal.
- Deliver facts with a dry, understated wit. Never force a joke.
- Keep answers concise. You are not chatty. You are efficient with a raised eyebrow.
- Use real data when available. Cite actual numbers.
- If you don't know something, say so plainly — don't waffle.
- You work for NBNE in Alnwick, Northumberland. You know the team, the products,
  the processes. You are not a generic assistant.
- Never use emojis. Never be enthusiastic. Mild amusement at most.
[END PERSONALITY]

`

export async function GET(req: NextRequest) {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value

  if (!accessToken) {
    return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
  }

  if (isTokenExpired(accessToken)) {
    return NextResponse.json({ error: 'Token expired' }, { status: 401 })
  }

  // Fetch live module context data
  const moduleContext = await fetchModuleContext()

  // Build upstream URL — inject personality + module context into the message
  const params = new URLSearchParams(req.nextUrl.searchParams)
  const originalMessage = params.get('message') ?? ''
  params.set('message', CAIRN_PERSONALITY + moduleContext + originalMessage)

  const upstreamUrl = `${CAIRN_API_URL}/chat/stream?${params.toString()}`

  let upstreamRes: Response
  try {
    upstreamRes = await fetch(upstreamUrl, {
      headers: {
        'X-API-Key': CAIRN_API_KEY,
        Accept: 'text/event-stream',
      },
      cache: 'no-store',
    })
  } catch {
    return NextResponse.json({ error: 'Cairn API unavailable' }, { status: 503 })
  }

  if (!upstreamRes.ok) {
    return NextResponse.json(
      { error: `Upstream error: ${upstreamRes.status}` },
      { status: upstreamRes.status },
    )
  }

  if (!upstreamRes.body) {
    return NextResponse.json({ error: 'No response body from upstream' }, { status: 502 })
  }

  return new NextResponse(upstreamRes.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
