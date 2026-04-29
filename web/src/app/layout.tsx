import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import Script from 'next/script'
import { NavBar } from '@/components/NavBar'
import { BRAND } from '@/lib/brand'
import './globals.css'

// ── Typography ─────────────────────────────────────────────────────────
// Inter for UI chrome, JetBrains Mono for code / identifiers / numerics.
// Both loaded via next/font so they're self-hosted, pre-rendered, and
// zero-layout-shift.

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
})

const jetbrains = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
})

// ── Metadata ───────────────────────────────────────────────────────────

export const metadata: Metadata = {
  title: `${BRAND} — Sovereign AI Agent`,
  description:
    'Counterfactual memory, email triage, principal-developer-grade code assistance.',
  applicationName: BRAND,
  manifest: '/manifest.webmanifest',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: BRAND,
  },
  icons: {
    icon: [
      { url: '/favicon.ico', sizes: 'any' },
      { url: '/favicon.png', type: 'image/png', sizes: '32x32' },
      { url: '/icon-192.png', type: 'image/png', sizes: '192x192' },
      { url: '/icon-512.png', type: 'image/png', sizes: '512x512' },
    ],
    apple: [{ url: '/apple-touch-icon.png', sizes: '180x180' }],
  },
  formatDetection: {
    telephone: false,
    email: false,
    address: false,
  },
}

export const viewport: Viewport = {
  themeColor: '#0f172a',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
  viewportFit: 'cover',
}

// ── Root layout ────────────────────────────────────────────────────────

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrains.variable} h-full`}
      suppressHydrationWarning
    >
      <body className="flex h-full flex-col font-sans antialiased">
        <NavBar />
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        {/* Service worker — temporarily disabled (2026-04-29).
            iOS Safari was silently dropping session cookies on the
            redirect-after-login path when an SW was caching navigation
            responses. Login bouncing was the symptom. We unregister
            any pre-existing SW (clients who already installed it on
            an earlier visit) and DO NOT register a new one. The PWA
            install flow keeps working because Add-to-Home-Screen
            doesn't require an SW — only the manifest.webmanifest
            does, and that's still served. Re-enable once the auth
            path is confirmed stable on every target device. */}
        <Script id="deek-sw-unregister" strategy="afterInteractive">
          {`
            if ('serviceWorker' in navigator) {
              navigator.serviceWorker.getRegistrations().then(function(rs) {
                rs.forEach(function(r) { r.unregister(); });
              }).catch(function() {});
              if (window.caches && caches.keys) {
                caches.keys().then(function(keys) {
                  keys.forEach(function(k) { caches.delete(k); });
                }).catch(function() {});
              }
            }
          `}
        </Script>
      </body>
    </html>
  )
}
