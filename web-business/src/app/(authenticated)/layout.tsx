'use client'

import { useState } from 'react'
import AuthProvider from '@/components/auth/AuthProvider'
import Sidebar from '@/components/layout/Sidebar'
import HeaderWrapper from '@/components/layout/HeaderWrapper'

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <AuthProvider>
      <div className="min-h-screen bg-slate-50">
        <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <HeaderWrapper onMenuToggle={() => setSidebarOpen((prev) => !prev)} />
        {/* Main content:
            Desktop: offset by sidebar width (240px) and header height (56px)
            Mobile:  no left offset, same top offset */}
        <main className="flex-1 overflow-y-auto bg-slate-50 mt-14 p-4 md:p-6 md:ml-[240px]">
          {children}
        </main>
      </div>
    </AuthProvider>
  )
}
