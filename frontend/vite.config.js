import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/** IPv4 evita falha quando `localhost` resolve para ::1 e o uvicorn só escuta em IPv4. */
const defaultProxyTarget = 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = (env.VITE_PROXY_TARGET || defaultProxyTarget).replace(/\/$/, '')

  const proxy = {
    '/api': {
      target,
      changeOrigin: true,
    },
  }

  return {
    plugins: [react()],
    server: {
      host: true,
      port: 5174,
      strictPort: true,
      proxy,
    },
    preview: {
      host: true,
      port: 5174,
      strictPort: true,
      proxy,
    },
  }
})
