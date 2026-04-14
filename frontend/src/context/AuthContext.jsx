import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import { authApi } from '../api/auth.js'
import { clearAuthToken, getAuthToken, setAuthToken } from '../api/client.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [me, setMe] = useState(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    const t = getAuthToken()
    if (!t) {
      setMe(null)
      setLoading(false)
      return
    }
    try {
      const m = await authApi.me()
      setMe(m)
    } catch {
      clearAuthToken()
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const h = () => {
      clearAuthToken()
      setMe(null)
    }
    window.addEventListener('bgp-manager-auth-expired', h)
    return () => window.removeEventListener('bgp-manager-auth-expired', h)
  }, [])

  const login = useCallback(async (username, password) => {
    const { access_token: token } = await authApi.login(username, password)
    setAuthToken(token)
    const m = await authApi.me()
    setMe(m)
    return m
  }, [])

  const logout = useCallback(() => {
    clearAuthToken()
    setMe(null)
  }, [])

  const hasPermission = useCallback(
    perm => Boolean(me?.permissions?.includes(perm)),
    [me],
  )

  const value = useMemo(
    () => ({ me, loading, login, logout, refresh, hasPermission }),
    [me, loading, login, logout, refresh, hasPermission],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth deve ser usado dentro de AuthProvider')
  return ctx
}
