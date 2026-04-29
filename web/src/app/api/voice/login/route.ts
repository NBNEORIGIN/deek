/**
 * POST /api/voice/login — verify credentials against DEEK_USERS env var,
 * issue a HS256 JWT cookie on success.
 *
 * Accepts BOTH application/json and application/x-www-form-urlencoded
 * bodies. JSON callers get a JSON response; form callers get a 303
 * redirect to the callbackUrl (or /voice).
 *
 * Form-POST path exists because iOS Safari 17+ silently drops cookies
 * set by fetch() when the host is a bare IP address (Rex tailnet
 * deployment hit this on 2026-04-29: login API returned 200 + cookie
 * but Safari dropped it before the JS-driven window.location.href
 * fired). Full-page form navigation isn't subject to the same
 * Intelligent Tracking Prevention path, so the cookie sticks.
 */
import { NextResponse } from 'next/server'
import {
  verifyCredentials,
  issueSessionToken,
  sessionCookieOptions,
} from '@/lib/auth'

export const dynamic = 'force-dynamic'

export async function POST(req: Request) {
  const contentType = (req.headers.get('content-type') || '').toLowerCase()
  const isForm = contentType.includes('application/x-www-form-urlencoded')

  let email = ''
  let password = ''
  let callbackUrl = '/voice'

  if (isForm) {
    const fd = await req.formData()
    email = String(fd.get('email') || '').trim()
    password = String(fd.get('password') || '')
    callbackUrl = String(fd.get('callbackUrl') || '/voice') || '/voice'
  } else {
    let body: any
    try {
      body = await req.json()
    } catch {
      return NextResponse.json({ error: 'invalid_json' }, { status: 400 })
    }
    email = String(body?.email || '').trim()
    password = String(body?.password || '')
  }

  // Defensive — only allow same-origin relative paths as the redirect target.
  if (!callbackUrl.startsWith('/') || callbackUrl.startsWith('//')) {
    callbackUrl = '/voice'
  }

  // Build redirect URLs from the *external* Host (preserved by nginx),
  // not from req.url which resolves to the internal container URL like
  // http://0.0.0.0:3000 — that's never reachable from Jo's phone.
  const proto = req.headers.get('x-forwarded-proto') || 'http'
  const host = req.headers.get('host') || 'localhost'
  const externalBase = `${proto}://${host}`

  const fail = (status: number, message: string) => {
    if (isForm) {
      const url = new URL('/voice/login', externalBase)
      url.searchParams.set('error', message)
      url.searchParams.set('callbackUrl', callbackUrl)
      return NextResponse.redirect(url, { status: 303 })
    }
    return NextResponse.json({ error: message }, { status })
  }

  if (!email || !password) {
    return fail(400, 'email and password required')
  }

  const user = await verifyCredentials(email, password)
  if (!user) {
    return fail(401, 'Invalid email or password.')
  }

  const token = await issueSessionToken(user)
  const opts = sessionCookieOptions()

  let res: NextResponse
  if (isForm) {
    const target = new URL(callbackUrl, externalBase)
    res = NextResponse.redirect(target, { status: 303 })
  } else {
    res = NextResponse.json({
      ok: true,
      user: { email: user.email, name: user.name, role: user.role },
    })
  }
  res.cookies.set(opts.name, token, {
    httpOnly: opts.httpOnly,
    sameSite: opts.sameSite,
    path: opts.path,
    secure: opts.secure,
    maxAge: opts.maxAge,
  })
  return res
}
