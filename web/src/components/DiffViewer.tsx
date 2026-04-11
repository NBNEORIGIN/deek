'use client'

interface DiffViewerProps {
  diff: string
}

export function DiffViewer({ diff }: DiffViewerProps) {
  if (!diff) return null

  const lines = diff.split('\n')

  return (
    <pre className="max-h-48 overflow-y-auto rounded-md border border-slate-800 bg-slate-950 p-3 font-mono text-2xs leading-5">
      {lines.map((line, i) => {
        let cls = 'text-slate-300'
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'text-emerald-400'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'text-red-400'
        else if (line.startsWith('@@')) cls = 'text-blue-400'
        else if (line.startsWith('---') || line.startsWith('+++')) cls = 'text-slate-500'
        return (
          <span key={i} className={cls + ' block'}>
            {line || ' '}
          </span>
        )
      })}
    </pre>
  )
}
