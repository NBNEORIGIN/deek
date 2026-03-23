'use client'

interface DiffViewerProps {
  diff: string
}

export function DiffViewer({ diff }: DiffViewerProps) {
  if (!diff) return null

  const lines = diff.split('\n')

  return (
    <pre className="text-xs font-mono bg-zinc-900 border border-zinc-700 rounded p-3 max-h-48 overflow-y-auto leading-5">
      {lines.map((line, i) => {
        let cls = 'text-zinc-400'
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'text-emerald-400'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'text-red-400'
        else if (line.startsWith('@@')) cls = 'text-blue-400'
        else if (line.startsWith('---') || line.startsWith('+++')) cls = 'text-zinc-500'
        return (
          <span key={i} className={cls + ' block'}>
            {line || ' '}
          </span>
        )
      })}
    </pre>
  )
}
