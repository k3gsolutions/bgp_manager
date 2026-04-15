import axios from 'axios'

/**
 * Base da API HTTP.
 * - Vazio: usa `/api` (Vite dev/preview encaminha para o backend — ver vite.config.js).
 * - Definido: URL absoluta do backend, ex. `http://127.0.0.1:8000` (requer CORS no FastAPI).
 */
const root = (import.meta.env.VITE_API_URL || '').trim().replace(/\/$/, '')
export const apiBaseURL = root ? `${root}/api` : '/api'

export const api = axios.create({ baseURL: apiBaseURL })

const TOKEN_KEY = 'bgp_manager_token'

export function getAuthToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setAuthToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export function clearAuthToken() {
  setAuthToken('')
}

api.interceptors.request.use((config) => {
  const t = getAuthToken()
  if (t) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${t}`
  }
  return config
})

api.interceptors.response.use(
  r => r,
  err => {
    if (err?.response?.status === 401) {
      clearAuthToken()
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('bgp-manager-auth-expired'))
      }
    }
    return Promise.reject(err)
  },
)

function looksLikeNoHttpResponse(err) {
  if (err?.response) return false
  const code = err?.code
  if (code === 'ERR_NETWORK' || code === 'ECONNABORTED' || code === 'ECONNREFUSED') return true
  const raw = String(err?.message || '').trim()
  const lower = raw.toLowerCase()
  if (lower.includes('network error')) return true
  if (lower.includes('networkerror')) return true
  if (lower.includes('failed to fetch')) return true
  if (lower === 'load failed') return true
  if (lower.includes('erro de rede')) return true
  if (lower.includes('connection refused')) return true
  return false
}

/** Mensagem para logs/UI quando não há resposta HTTP (axios / fetch). */
export function formatAxiosError(err) {
  if (err?.response) return String(err?.message || 'Resposta HTTP com erro')
  if (!looksLikeNoHttpResponse(err)) {
    return String(err?.message || err?.code || 'erro')
  }
  const direct = (import.meta.env.VITE_API_URL || '').trim()
  if (direct) {
    return (
      `sem resposta em ${apiBaseURL} — verifique se o FastAPI está no ar (${direct}). ` +
      'CORS no backend deve permitir a origem desta página (URL do Vite no navegador), não a URL do API.'
    )
  }
  return (
    `sem resposta em ${apiBaseURL} — suba o FastAPI em :8000 e use «npm run dev» (proxy), ` +
    'ou defina VITE_API_URL=http://127.0.0.1:8000 no .env do frontend (reinicie o Vite).'
  )
}
