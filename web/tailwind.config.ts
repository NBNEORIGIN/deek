import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // Tighter type scale for dense UI
        '2xs': ['11px', { lineHeight: '14px' }],
      },
      borderRadius: {
        // Project convention: keep radii small. md (6px) and lg (8px) cover
        // 95% of cases; nothing bigger than xl (12px) in production UI.
        'md': '6px',
        'lg': '8px',
        'xl': '12px',
      },
      boxShadow: {
        // Quiet shadows — no blurry glows
        'subtle': '0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 1px 0 rgb(15 23 42 / 0.03)',
        'card':   '0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px 0 rgb(15 23 42 / 0.04)',
        'pop':    '0 8px 24px -8px rgb(15 23 42 / 0.15), 0 4px 8px -4px rgb(15 23 42 / 0.08)',
      },
      colors: {
        // Single accent alias so component code doesn't reach for arbitrary sky/blue shades
        accent: {
          DEFAULT: '#2563eb',  // blue-600
          hover:   '#1d4ed8',  // blue-700
          soft:    '#eff6ff',  // blue-50
          border:  '#bfdbfe',  // blue-200
        },
      },
    },
  },
  plugins: [],
}

export default config
