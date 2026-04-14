/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          main: '#0f111a',
          surface: '#161922',
          sidebar: '#1a1c2e',
          elevated: '#1e2035',
          border: '#252840',
        },
        brand: {
          blue: '#3b82f6',
          'blue-hover': '#2563eb',
          'blue-dim': 'rgba(59,130,246,0.12)',
        },
        ink: {
          primary: '#e2e8f0',
          secondary: '#94a3b8',
          muted: '#64748b',
        },
        status: {
          up: '#22c55e',
          down: '#ef4444',
          warn: '#f59e0b',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Courier New"', 'monospace'],
      },
    },
  },
  plugins: [],
}
