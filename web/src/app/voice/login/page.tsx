/**
 * /voice/login — self-contained login page.
 *
 * Renders a plain HTML form that POSTs application/x-www-form-urlencoded
 * to /api/voice/login. The route handler issues the session cookie and
 * 303-redirects to callbackUrl (full-page navigation, not JS).
 *
 * This is a server component on purpose: a JS fetch + window.location.href
 * pattern was silently dropping the session cookie on iOS Safari 17+
 * over HTTP-bare-IP (the tailnet Rex deployment, 2026-04-29). Form POST +
 * server redirect bypasses the Intelligent Tracking Prevention path
 * cleanly.
 *
 * The password field uses a small client component (PasswordField.tsx)
 * for the show/hide eye toggle — that's the only JS on this page.
 */
import { PasswordField } from './PasswordField'

interface SearchParams {
  callbackUrl?: string
  error?: string
}

export default function LoginPage({
  searchParams,
}: {
  searchParams: SearchParams
}) {
  const callbackUrl = searchParams.callbackUrl || '/voice'
  const error = searchParams.error
  const brand = process.env.NEXT_PUBLIC_DEEK_BRAND_NAME || 'Deek'

  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center bg-white p-6 text-gray-900">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="text-3xl font-semibold">{brand}</div>
          <div className="mt-1 text-sm text-gray-500">Sign in to continue</div>
        </div>

        <form
          method="POST"
          action="/api/voice/login"
          className="space-y-3"
          autoComplete="on"
        >
          <input type="hidden" name="callbackUrl" value={callbackUrl} />
          <div>
            <label
              htmlFor="email"
              className="mb-1 block text-xs uppercase tracking-wider text-gray-500"
            >
              Email
            </label>
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              required
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-base text-gray-900 placeholder-gray-400 focus:border-gray-500 focus:outline-none focus:ring-1 focus:ring-gray-400"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="mb-1 block text-xs uppercase tracking-wider text-gray-500"
            >
              Password
            </label>
            <PasswordField />
          </div>

          {error && (
            <div className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
              {error}
            </div>
          )}

          <button
            type="submit"
            className="w-full rounded-lg bg-gray-900 py-3 text-base font-semibold text-white transition hover:bg-gray-800 disabled:opacity-50"
          >
            Sign in
          </button>
        </form>

        <div className="mt-8 text-center text-xs text-gray-500">
          Access is limited. Speak to Toby if you need an account.
        </div>
      </div>
    </div>
  )
}
