'use client'

import ModuleMap from '@/components/map/ModuleMap'

export default function MapPage() {
  return (
    <div>
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-slate-900">Module Map</h1>
        <p className="text-sm text-slate-500 mt-1">
          Interactive view of the NBNE ecosystem. Click a node to read its wiki article.
        </p>
      </div>
      <ModuleMap />
    </div>
  )
}
