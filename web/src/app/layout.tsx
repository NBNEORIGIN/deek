import type { Metadata, Viewport } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import Script from 'next/script'
import { NavBar } from '@/components/NavBar'
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
  title: 'Cairn — Sovereign AI Agent',
  description:
    'Counterfactual memory, email triage, principal-developer-grade code assistance for NBNE.',
  applicationName: 'Cairn',
  manifest: '/manifest.webmanifest',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'Cairn',
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
        {/* Service worker registration — runs after hydration, strategy
            "afterInteractive" so it never blocks the first paint. */}
        <Script id="cairn-sw-register" strategy="afterInteractive">
          {`
            if ('serviceWorker' in navigator) {
              window.addEventListener('load', function() {
                navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function(err) {
                  console.warn('Cairn SW registration failed:', err);
                });
              });
            }
          `}
        </Script>
      </body>
    </html>
  )
}
