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
  const [ok, setOk] = useState('')
  const [name, setName] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)

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
      setErr('')
      await companiesApi.create({ name: name.trim() })
      setOk('Empresa criada com sucesso.')
      setName('')
      setShowCreateModal(false)
      await load()
    } catch (ex) {
      setOk('')
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
        {canCreate && (
          <button
            type="button"
            onClick={() => {
              setErr('')
              setOk('')
              setName('')
              setShowCreateModal(true)
            }}
            className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold"
          >
            <Plus size={14} />
            Criar
          </button>
        )}
      </div>
      {err && (
        <div className="text-[12px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2">
          {err}
        </div>
      )}
      {ok && (
        <div className="text-[12px] text-emerald-300 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-3 py-2">
          {ok}
        </div>
      )}
      {canCreate && showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form
            onSubmit={handleCreate}
            className="w-full max-w-md rounded-xl border border-[#252840] bg-[#11131a] p-4 space-y-3 shadow-2xl"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-[14px] font-semibold text-ink-primary">Nova empresa</h2>
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="text-[11px] text-ink-muted hover:text-ink-primary"
              >
                Fechar
              </button>
            </div>
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full bg-[#161922] border border-[#252840] rounded-lg px-3 py-2 text-[12px] text-ink-primary"
              placeholder="Nome da empresa"
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="px-3 py-1.5 rounded-lg border border-[#252840] text-[12px] text-ink-secondary"
              >
                Cancelar
              </button>
              <button
                type="submit"
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold"
              >
                <Plus size={14} />
                Criar
              </button>
            </div>
          </form>
        </div>
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
