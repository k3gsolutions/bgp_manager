import { useCallback, useEffect, useState } from 'react'
import { Loader2, Shield, Trash2, UserPlus } from 'lucide-react'
import { usersApi } from '../api/users.js'
import { companiesApi } from '../api/companies.js'
import { formatAxiosError } from '../api/client.js'
import { useAuth } from '../context/AuthContext.jsx'

const ROLES = ['viewer', 'operator', 'admin', 'superadmin']

export default function UsersPage() {
  const { hasPermission, me } = useAuth()
  const [rows, setRows] = useState([])
  const [companies, setCompanies] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [form, setForm] = useState({
    username: '',
    password: '',
    role: 'viewer',
    company_ids: [],
    access_all_companies: false,
  })

  const load = useCallback(async () => {
    setErr('')
    setLoading(true)
    try {
      const [u, c] = await Promise.all([usersApi.list(), companiesApi.list().catch(() => [])])
      setRows(u)
      setCompanies(c)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const isSuperAdmin = (me?.role || '').toLowerCase() === 'superadmin'
  const isSuperRole = (form.role || '').toLowerCase() === 'superadmin'

  async function handleCreate(e) {
    e.preventDefault()
    const wantAll = !isSuperRole && form.access_all_companies && isSuperAdmin
    if (!isSuperRole && !wantAll && form.company_ids.length === 0) {
      setErr('Selecione ao menos uma empresa ou marque “Todos os clientes”.')
      return
    }
    try {
      await usersApi.create({
        username: form.username.trim(),
        password: form.password,
        role: form.role,
        is_active: true,
        company_ids: wantAll ? [] : form.company_ids.map(Number),
        access_all_companies: wantAll,
      })
      setForm({
        username: '',
        password: '',
        role: 'viewer',
        company_ids: [],
        access_all_companies: false,
      })
      setErr('')
      await load()
    } catch (ex) {
      setErr(formatAxiosError(ex))
    }
  }

  async function handleDelete(id) {
    if (id === me?.id) {
      setErr('Não é possível excluir o próprio usuário.')
      return
    }
    if (!window.confirm('Excluir usuário?')) return
    try {
      await usersApi.remove(id)
      await load()
    } catch (ex) {
      setErr(formatAxiosError(ex))
    }
  }

  const canCreate = hasPermission('users.create')
  const canDelete = hasPermission('users.delete')

  function toggleCompany(cid) {
    setForm(f => {
      const s = new Set(f.company_ids)
      if (s.has(cid)) s.delete(cid)
      else s.add(cid)
      return { ...f, company_ids: [...s] }
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Shield size={18} className="text-ink-secondary" />
        <h1 className="text-[18px] font-bold text-ink-primary">Usuários</h1>
      </div>
      {err && (
        <div className="text-[12px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2">
          {err}
        </div>
      )}
      {canCreate && (
        <form onSubmit={handleCreate} className="bg-[#161922] border border-[#252840] rounded-xl p-4 space-y-3">
          <p className="text-[11px] text-ink-muted font-semibold uppercase tracking-wider">Novo usuário</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <input
              placeholder="Username"
              value={form.username}
              onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
              className="bg-[#13151f] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px]"
            />
            <input
              type="password"
              placeholder="Senha (mín. 8)"
              value={form.password}
              onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
              className="bg-[#13151f] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px]"
            />
            <select
              value={form.role}
              onChange={e =>
                setForm(f => {
                  const role = e.target.value
                  const sup = (role || '').toLowerCase() === 'superadmin'
                  return {
                    ...f,
                    role,
                    company_ids: sup ? [] : f.company_ids,
                    access_all_companies: sup ? false : f.access_all_companies,
                  }
                })
              }
              className="bg-[#13151f] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px]"
            >
              {ROLES.map(r => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          {isSuperRole && (
            <p className="text-[11px] text-ink-muted">
              Superadmin tem acesso implícito a todos os clientes e equipamentos.
            </p>
          )}
          {!isSuperRole && companies.length > 0 && (
            <div className="space-y-2">
              <p className="text-[10px] text-ink-muted mb-1">Clientes / empresas</p>
              <label className="flex items-center gap-2 text-[11px] text-ink-secondary cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.access_all_companies}
                  disabled={!isSuperAdmin}
                  title={
                    !isSuperAdmin
                      ? 'Apenas superadmin pode conceder acesso a todos os clientes'
                      : undefined
                  }
                  onChange={e =>
                    setForm(f => ({
                      ...f,
                      access_all_companies: e.target.checked,
                      company_ids: e.target.checked ? [] : f.company_ids,
                    }))
                  }
                />
                Todos os clientes
              </label>
              <div className="flex flex-wrap gap-2">
                {companies.map(c => (
                  <label
                    key={c.id}
                    className={`flex items-center gap-1 text-[11px] text-ink-secondary ${
                      form.access_all_companies ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
                    }`}
                  >
                    <input
                      type="checkbox"
                      disabled={form.access_all_companies}
                      checked={form.company_ids.includes(c.id)}
                      onChange={() => toggleCompany(c.id)}
                    />
                    {c.name}
                  </label>
                ))}
              </div>
            </div>
          )}
          <button
            type="submit"
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold"
          >
            <UserPlus size={14} />
            Criar
          </button>
        </form>
      )}
      {loading ? (
        <div className="flex items-center gap-2 text-ink-muted py-8">
          <Loader2 size={16} className="animate-spin" />
          Carregando…
        </div>
      ) : (
        <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-x-auto">
          <table className="w-full text-[12px] min-w-[480px]">
            <thead>
              <tr className="border-b border-[#1e2235] text-left text-[10px] uppercase text-[#4a5568]">
                <th className="px-4 py-2">Usuário</th>
                <th className="px-4 py-2">Papel</th>
                <th className="px-4 py-2">Empresas</th>
                <th className="px-4 py-2">Ativo</th>
                {canDelete && <th className="px-4 py-2 w-20" />}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-b border-[#1e2235] last:border-0">
                  <td className="px-4 py-2 font-mono text-ink-primary">{r.username}</td>
                  <td className="px-4 py-2 text-ink-secondary">{r.role}</td>
                  <td className="px-4 py-2 text-ink-muted">
                    {r.access_all_companies
                      ? 'Todos os clientes'
                      : (r.company_ids || []).join(', ') || '—'}
                  </td>
                  <td className="px-4 py-2">{r.is_active ? 'sim' : 'não'}</td>
                  {canDelete && (
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        disabled={r.id === me?.id}
                        onClick={() => handleDelete(r.id)}
                        className="p-1.5 rounded text-rose-300 hover:bg-rose-500/10 disabled:opacity-30"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
