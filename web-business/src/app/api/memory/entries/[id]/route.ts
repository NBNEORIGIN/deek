import { cookies } from 'next/headers'
import { NextRequest, NextResponse } from 'next/server'
import { cairnFetch } from '@/lib/api'
import { AUTH_COOKIE_NAME, isTokenExpired } from '@/lib/auth'

async function getAuth() {
  const cookieStore = await cookies()
  const accessToken = cookieStore.get(AUTH_COOKIE_NAME)?.value
  return accessToken
}

function unauthorized() {
  return NextResponse.json({ error: 'Not authenticated' }, { status: 401 })
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const token = await getAuth()
  if (!token || isTokenExpired(token)) return unauthorized()

  const { id } = await params
  const project = req.nextUrl.searchParams.get('project') ?? 'nbne'

  try {
    const cairnRes = await cairnFetch(`/memory/entries/${id}?project=${project}`, {
      cache: 'no-store',
    })
    const data = await cairnRes.json()
    return NextResponse.json(data, { status: cairnRes.status })
  } catch {
    return NextResponse.json({ error: 'Failed to fetch entry' }, { status: 500 })
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const token = await getAuth()
  if (!token || isTokenExpired(token)) return unauthorized()

  const { id } = await params
  const project = req.nextUrl.searchParams.get('project') ?? 'nbne'

  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  try {
    const cairnRes = await cairnFetch(`/memory/entries/${id}?project=${project}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await cairnRes.json()
    return NextResponse.json(data, { status: cairnRes.status })
  } catch {
    return NextResponse.json({ error: 'Failed to update entry' }, { status: 500 })
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const token = await getAuth()
  if (!token || isTokenExpired(token)) return unauthorized()

  const { id } = await params
  const project = req.nextUrl.searchParams.get('project') ?? 'nbne'

  try {
    const cairnRes = await cairnFetch(`/memory/entries/${id}?project=${project}`, {
      method: 'DELETE',
    })
    if (cairnRes.status === 204) {
      return new NextResponse(null, { status: 204 })
    }
    const data = await cairnRes.json()
    return NextResponse.json(data, { status: cairnRes.status })
  } catch {
    return NextResponse.json({ error: 'Failed to delete entry' }, { status: 500 })
  }
}
