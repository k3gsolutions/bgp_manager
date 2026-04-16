import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { communitiesApi } from '../api/communities.js'
import { formatAxiosError } from '../api/client.js'

export default function CommunityLibraryTable({ deviceId, canResync, onLog }) {
  const latestDeviceId = useRef(deviceId)
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [resyncing, setResyncing] = useState(false)
  const [resyncingLive, setResyncingLive] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    latestDeviceId.current = deviceId
  }, [deviceId])

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300)
    return () => clearTimeout(t)
  }, [q])

  const load = useCallback(async () => {
    const targetId = deviceId
    setLoading(true)
    setErr('')
    try {
      const data = await communitiesApi.library(targetId, debounced ? { q: debounced } : {})
      if (latestDeviceId.current !== targetId) return
      const list = Array.isArray(data) ? data : []
      setRows(list.filter((r) => r.device_id === targetId))
    } catch (e) {
      if (latestDeviceId.current !== targetId) return
      setErr(formatAxiosError(e))
    } finally {
      if (latestDeviceId.current === targetId) setLoading(false)
    }
  }, [deviceId, debounced])

  useEffect(() => {
    load()
  }, [load])

  async function resync() {
    if (!canResync) return
    const targetId = deviceId
    setResyncing(true)
    setErr('')
    try {
      const stats = await communitiesApi.resyncFromConfig(targetId)
      if (latestDeviceId.current !== targetId) return
      onLog?.('info', `Biblioteca: resync backup ${JSON.stringify(stats)}`)
      await load()
    } catch (e) {
      if (latestDeviceId.current === targetId) setErr(formatAxiosError(e))
    } finally {
      if (latestDeviceId.current === targetId) setResyncing(false)
    }
  }

  async function resyncLive() {
    if (!canResync) return
    const targetId = deviceId
    setResyncingLive(true)
    setErr('')
    try {
      const stats = await communitiesApi.resyncLive(targetId)
      if (latestDeviceId.current !== targetId) return
      onLog?.('info', `Biblioteca: resync live (SSH) ${JSON.stringify(stats)}`)
      await load()
    } catch (e) {
      if (latestDeviceId.current === targetId) setErr(formatAxiosError(e))
    } finally {
      if (latestDeviceId.current === targetId) setResyncingLive(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[11px] text-ink-muted leading-relaxed">
        Sincronizar a partir do <strong className="text-ink-secondary">último backup</strong> (running-config na BD) ou
        opcionalmente <strong className="text-ink-secondary">live</strong> por SSH. O sync separa{' '}
        <span className="font-mono text-ink-secondary">ip community-filter</span> → esta biblioteca;{' '}
        <span className="font-mono text-ink-secondary">ip community-list</span> → aba Community Sets.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar por nome, valor ou descrição…"
            className="w-full pl-8 pr-3 py-2 rounded-lg bg-[#161922] border border-[#252840] text-[13px] text-ink-primary placeholder:text-ink-muted focus:border-brand-blue outline-none"
          />
        </div>
        {canResync && (
          <>
            <button
              type="button"
              disabled={resyncing}
              onClick={resync}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-secondary hover:border-brand-blue hover:text-brand-blue transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={resyncing ? 'animate-spin' : ''} />
              Sync backup
            </button>
            <button
              type="button"
              disabled={resyncingLive}
              onClick={resyncLive}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-secondary hover:border-sky-500/50 hover:text-sky-300 transition-colors disabled:opacity-50"
              title="display current-configuration por SSH — inventário complementar"
            >
              <RefreshCw size={14} className={resyncingLive ? 'animate-spin' : ''} />
              Sync live (SSH)
            </button>
          </>
        )}
      </div>

      {err && (
        <div className="text-[12px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
          {err}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-[#252840]">
        <table className="w-full text-left text-[12px]">
          <thead className="bg-[#161922] text-ink-muted uppercase tracking-wide text-[10px]">
            <tr>
              <th className="px-3 py-2 font-semibold">Filter</th>
              <th className="px-3 py-2 font-semibold">Valor</th>
              <th className="px-3 py-2 font-semibold min-w-[140px]">Descrição</th>
              <th className="px-3 py-2 font-semibold">Tipo</th>
              <th className="px-3 py-2 font-semibold">Ação</th>
              <th className="px-3 py-2 font-semibold">Origem</th>
              <th className="px-3 py-2 font-semibold">Estado</th>
              <th className="px-3 py-2 font-semibold text-right">Uso (RP)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#252840]">
            {loading ? (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-ink-muted">
                  A carregar…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-ink-muted">
                  Sem entradas. Sincronize após existir running-config no equipamento.
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className="hover:bg-[#1a1d2e]">
                  <td className="px-3 py-2 text-ink-primary font-mono text-[11px]">{r.filter_name ?? r.name}</td>
                  <td className="px-3 py-2 text-ink-secondary font-mono text-[11px]">{r.community_value}</td>
                  <td className="px-3 py-2 text-[10px] text-ink-muted max-w-[220px] truncate" title={r.description || ''}>
                    {r.description || '—'}
                  </td>
                  <td className="px-3 py-2 text-ink-muted">{r.match_type}</td>
                  <td className="px-3 py-2 text-ink-muted">{r.action || '—'}</td>
                  <td className="px-3 py-2 text-ink-muted">{r.origin}</td>
                  <td className="px-3 py-2 text-ink-muted">
                    {r.is_active === false ? (
                      <span className="text-amber-400/90 text-[10px]">inativo</span>
                    ) : (
                      <span className="text-emerald-400/80 text-[10px]">ativo</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right text-ink-secondary">{r.usage_count ?? 0}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
