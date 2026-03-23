import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'CLAW — Sovereign AI Agent',
  description: 'Per-project AI coding agent with permanent context',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full">{children}</body>
    </html>
  )
}
