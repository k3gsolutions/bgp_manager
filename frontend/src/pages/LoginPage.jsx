import { useState } from 'react'
import { Loader2, Zap } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import { formatAxiosError } from '../api/client.js'

export default function LoginPage() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function onSubmit(e) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      await login(username.trim(), password)
    } catch (ex) {
      const d = ex?.response?.data?.detail
      setErr(typeof d === 'string' ? d : formatAxiosError(ex))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0f111a] flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-[#11141f] border border-[#2b3046] rounded-xl shadow-xl p-8">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-lg bg-brand-blue flex items-center justify-center">
            <Zap size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-[18px] font-bold text-ink-primary">BGP Manager</h1>
            <p className="text-[11px] text-ink-muted">Entre com seu usuário</p>
          </div>
        </div>
        <form onSubmit={onSubmit} className="space-y-4">
          {err && (
            <div className="text-[12px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2">
              {err}
            </div>
          )}
          <div>
            <label className="block text-[11px] text-ink-muted mb-1">Usuário</label>
            <input
              autoComplete="username"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full bg-[#161922] border border-[#252840] rounded-lg px-3 py-2 text-[13px] text-ink-primary outline-none focus:border-brand-blue"
            />
          </div>
          <div>
            <label className="block text-[11px] text-ink-muted mb-1">Senha</label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-[#161922] border border-[#252840] rounded-lg px-3 py-2 text-[13px] text-ink-primary outline-none focus:border-brand-blue"
            />
          </div>
          <button
            type="submit"
            disabled={busy || !username.trim() || !password}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg bg-brand-blue text-white text-[13px] font-semibold hover:bg-brand-blue-hover disabled:opacity-50"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : null}
            Entrar
          </button>
        </form>
        <p className="text-[10px] text-ink-muted mt-6 text-center">
          Primeiro acesso: defina <span className="font-mono">BOOTSTRAP_SUPERADMIN_PASSWORD</span> no backend ou use a senha padrão de desenvolvimento documentada no CHANGELOG.
        </p>
      </div>
    </div>
  )
}
