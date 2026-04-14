import { useCallback, useEffect, useState } from 'react'
import { Building2, Loader2, Plus, Trash2 } from 'lucide-react'
import { companiesApi } from '../api/companies.js'
import { formatAxiosError } from '../api/client.js'
import { useAuth } from '../context/AuthContext.jsx'

export default function CompaniesPage() {
  const { hasPermission } = useAuth()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [name, setName] = useState('')

  const load = useCallback(async () => {
    setErr('')
    setLoading(true)
    try {
      const data = await companiesApi.list()
      setRows(data)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleCreate(e) {
    e.preventDefault()
    if (!name.trim()) return
    try {
      await companiesApi.create({ name: name.trim() })
      setName('')
      await load()
    } catch (ex) {
      setErr(formatAxiosError(ex))
    }
  }

  async function handleDelete(id) {
    if (!window.confirm('Excluir esta empresa?')) return
    try {
      await companiesApi.remove(id)
      await load()
    } catch (ex) {
      setErr(formatAxiosError(ex))
    }
  }

  const canCreate = hasPermission('companies.create')
  const canDelete = hasPermission('companies.delete')

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Building2 size={18} className="text-ink-secondary" />
        <h1 className="text-[18px] font-bold text-ink-primary">Empresas</h1>
      </div>
      {err && (
        <div className="text-[12px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2">
          {err}
        </div>
      )}
      {canCreate && (
        <form onSubmit={handleCreate} className="flex gap-2 items-end flex-wrap">
          <div>
            <label className="block text-[10px] text-ink-muted mb-0.5">Nova empresa</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px] text-ink-primary w-64"
              placeholder="Nome"
            />
          </div>
          <button
            type="submit"
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold"
          >
            <Plus size={14} />
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
        <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-hidden">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-[#1e2235] text-left text-[10px] uppercase text-[#4a5568]">
                <th className="px-4 py-2">ID</th>
                <th className="px-4 py-2">Nome</th>
                {canDelete && <th className="px-4 py-2 w-24" />}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} className="border-b border-[#1e2235] last:border-0">
                  <td className="px-4 py-2 font-mono text-ink-muted">{r.id}</td>
                  <td className="px-4 py-2 text-ink-primary">{r.name}</td>
                  {canDelete && (
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        onClick={() => handleDelete(r.id)}
                        className="p-1.5 rounded text-rose-300 hover:bg-rose-500/10"
                        title="Excluir"
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
