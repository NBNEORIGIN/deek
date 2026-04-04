import { NextResponse } from 'next/server'
import { CAIRN_API_URL, CAIRN_API_KEY } from '@/lib/api'

/**
 * Module registry — auto-discovered from Cairn project configs + known endpoints.
 * Each module has a name (display), key (data key), and a url to poll.
 *
 * To add a new module: add an entry here. The dashboard renders whatever responds.
 * Modules that don't respond get status: 'unavailable' — no errors, no breaking.
 */
interface ModuleSpec {
  key: string
  name: string
  url: string
}

// Build module list: Cairn API internal endpoints + external module endpoints
function getModules(): ModuleSpec[] {
  const cairn = CAIRN_API_URL
  return [
    // Amazon Intelligence — built into Cairn API
    { key: 'amazon', name: 'Amazon Intelligence', url: `${cairn}/ami/cairn/context` },
    // Etsy Intelligence — built into Cairn API
    { key: 'etsy', name: 'Etsy Sales', url: `${cairn}/etsy/cairn/context` },
    // Manufacturing — standalone module (when deployed)
    { key: 'manufacture', name: 'Manufacturing', url: 'http://host.docker.internal:8015/api/cairn/context' },
    // Ledger — on deploy_default network
    { key: 'ledger', name: 'Finance', url: 'http://ledger-backend-1:8001/api/cairn/context' },
    // CRM — NBNE business development platform (on deploy_default network via docker-compose)
    { key: 'crm', name: 'Customers', url: `${process.env.CRM_API_URL || 'http://crm-crm-1:3000'}/api/cairn/context` },
  ]
}

const TIMEOUT_MS = 3000

interface ModuleResult {
  key: string
  name: string
  status: 'live' | 'stale' | 'unavailable'
  generated_at: string
  summary: string
  data: Record<string, unknown> | null
}

async function fetchModule(spec: ModuleSpec): Promise<ModuleResult> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

  try {
    const res = await fetch(spec.url, {
      signal: controller.signal,
      cache: 'no-store',
      headers: {
        'X-API-Key': CAIRN_API_KEY,
        'Authorization': `Bearer ${CAIRN_API_KEY}`,
      },
    })
    clearTimeout(timer)

    if (!res.ok) {
      return { key: spec.key, name: spec.name, status: 'unavailable', generated_at: '', summary: '', data: null }
    }

    const body = await res.json()
    return {
      key: spec.key,
      name: spec.name,
      status: 'live',
      generated_at: body.generated_at ?? new Date().toISOString(),
      summary: body.summary_text ?? body.summary ?? '',
      data: body,
    }
  } catch {
    clearTimeout(timer)
    return { key: spec.key, name: spec.name, status: 'unavailable', generated_at: '', summary: '', data: null }
  }
}

export async function GET() {
  const modules = getModules()
  const results = await Promise.allSettled(modules.map(fetchModule))

  const output: Record<string, ModuleResult> = {}
  for (let i = 0; i < modules.length; i++) {
    const result = results[i]
    output[modules[i].key] = result.status === 'fulfilled'
      ? result.value
      : { key: modules[i].key, name: modules[i].name, status: 'unavailable', generated_at: '', summary: '', data: null }
  }

  return NextResponse.json(output)
}
