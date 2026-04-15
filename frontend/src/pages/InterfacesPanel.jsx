import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Loader2, GitBranch, Zap, Trash2 } from 'lucide-react'
import { snmpApi } from '../api/snmp.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useLog } from '../context/LogContext.jsx'
import { reportBackendLog } from '../utils/reportBackendLog.js'
import { mergeByStableId } from '../utils/inventoryMerge.js'

/** SNMP leve (status) em background; inventário completo só ao intervalo longo ou botão. */
const STATUS_REFRESH_MS = 3 * 60 * 1000
const FULL_COLLECT_BG_MS = 18 * 60 * 1000
const FIRST_STATUS_DELAY_MS = 900
const FIRST_FULL_COLLECT_DELAY_MS = 90 * 1000

const STATUS_CFG = {
  up:      { dot: 'bg-green-400 shadow-[0_0_4px_rgba(74,222,128,.5)]', text: 'text-green-400', label: 'Up' },
  down:    { dot: 'bg-red-400',    text: 'text-red-400',    label: 'Down' },
  testing: { dot: 'bg-yellow-400', text: 'text-yellow-400', label: 'Testing' },
  unknown: { dot: 'bg-gray-500',   text: 'text-gray-500',   label: 'Unknown' },
}

function canonicalInterfaceName(name) {
  let s = String(name || '').trim()
  let prev = null
  while (s && s !== prev) {
    prev = s
    s = s.replace(/\s*\([^()]*\)\s*$/, '').trim()
  }
  return s
}

function dedupeInterfacesByCanonicalName(rows) {
  const byBase = new Map()
  for (const row of rows || []) {
    const base = canonicalInterfaceName(row?.name)
    if (!base) continue
    const cur = byBase.get(base)
    const rank = x => [
      x?.is_active ? 1 : 0,
      String(x?.name || '').trim() === canonicalInterfaceName(x?.name) ? 1 : 0,
      String(x?.last_updated || ''),
    ]
    if (!cur || rank(row).join('|') > rank(cur).join('|')) {
      byBase.set(base, row)
    }
  }
  return [...byBase.values()].sort((a, b) => String(a.name || '').localeCompare(String(b.name || '')))
}

export default function InterfacesPanel({ device }) {
  const { addLog } = useLog()
  const { hasPermission } = useAuth()
  const [interfaces, setInterfaces] = useState([])
  const [loading, setLoading] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [error, setError] = useState(null)
  const [collectInfo, setCollectInfo] = useState(null)
  const [search, setSearch] = useState('')
  const [filterStatus, setFilterStatus] = useState('all')
  const [deactivatingInterfaceId, setDeactivatingInterfaceId] = useState(null)

  const load = useCallback(async (opts = {}) => {
    const quiet = opts.quiet === true
    const merge = opts.merge === true
    if (!quiet) {
      setLoading(true)
      setError(null)
    }
    try {
      const data = await snmpApi.interfaces(device.id)
      const next = dedupeInterfacesByCanonicalName(data)
      setInterfaces(prev => (merge ? mergeByStableId(prev, next) : next))
    } catch (e) {
      if (!quiet) {
        const msg = e?.response?.data?.detail || 'Erro ao carregar interfaces'
        setError(msg)
        addLog('error', 'SNMP', 'GET interfaces', msg)
      }
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [device.id, addLog])

  useEffect(() => {
    load()
  }, [load])

  /** SNMP em segundo plano: status-refresh (leve) + coleta completa rara; lista vem sempre do BD. */
  useEffect(() => {
    if (!device.snmp_community) return undefined
    const canRefresh = hasPermission('devices.snmp_refresh')
    const canCollect = hasPermission('devices.snmp_collect')
    if (!canRefresh && !canCollect) return undefined
    let cancelled = false

    const runStatus = async () => {
      if (!canRefresh || cancelled || document.visibilityState === 'hidden') return
      try {
        await snmpApi.statusRefresh(device.id)
        if (cancelled) return
        await load({ quiet: true, merge: true })
      } catch {
        /* silencioso em background */
      }
    }

    const runFull = async () => {
      if (!canCollect || cancelled || document.visibilityState === 'hidden') return
      try {
        const info = await snmpApi.collect(device.id)
        if (cancelled) return
        reportBackendLog(addLog, 'SNMP', 'Coleta SNMP (fundo) — interfaces', info.log)
        setCollectInfo(info)
        await load({ quiet: true, merge: false })
      } catch {
        /* silencioso */
      }
    }

    const tStatus = canRefresh
      ? window.setTimeout(() => {
          void runStatus()
        }, FIRST_STATUS_DELAY_MS)
      : null
    const iStatus = canRefresh
      ? window.setInterval(() => {
          void runStatus()
        }, STATUS_REFRESH_MS)
      : null
    const tFull = canCollect
      ? window.setTimeout(() => {
          void runFull()
        }, FIRST_FULL_COLLECT_DELAY_MS)
      : null
    const iFull = canCollect
      ? window.setInterval(() => {
          void runFull()
        }, FULL_COLLECT_BG_MS)
      : null

    return () => {
      cancelled = true
      if (tStatus != null) window.clearTimeout(tStatus)
      if (iStatus != null) window.clearInterval(iStatus)
      if (tFull != null) window.clearTimeout(tFull)
      if (iFull != null) window.clearInterval(iFull)
    }
  }, [device.id, device.snmp_community, load, addLog, hasPermission])

  async function handleCollect() {
    if (!device.snmp_community) {
      setError('Community SNMP não configurada para este dispositivo')
      return
    }
    setCollecting(true)
    setError(null)
    const label = device.name || device.ip_address
    addLog('info', 'SNMP', `Coleta (interfaces) solicitada: ${label}`)
    try {
      const info = await snmpApi.collect(device.id)
      reportBackendLog(addLog, 'SNMP', `Trilha backend — coleta ${label}`, info.log)
      addLog(
        'success',
        'SNMP',
        `Coleta OK: ${info.interface_count} interfaces, ${info.bgp_peer_count} peers BGP, IPv6=${info.ipv6_address_count ?? 0} (${info.ipv6_source || 'snmp'})`,
      )
      setCollectInfo(info)
      await load({ merge: false })
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Erro na coleta SNMP'
      setError(msg)
      addLog('error', 'SNMP', msg)
    } finally {
      setCollecting(false)
    }
  }

  async function handleDeactivateInterface(iface) {
    if (!window.confirm(`Desativar interface ${iface.name} no banco?`)) return
    setDeactivatingInterfaceId(iface.id)
    setError(null)
    try {
      await snmpApi.deactivateInterface(device.id, iface.id)
      setInterfaces(prev => prev.map(x => (x.id === iface.id ? { ...x, is_active: false } : x)))
      addLog('warning', 'SNMP', `Interface desativada no banco: ${iface.name}`)
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Erro ao desativar interface'
      setError(msg)
      addLog('error', 'SNMP', msg)
    } finally {
      setDeactivatingInterfaceId(null)
    }
  }

  const filtered = interfaces.filter(i => {
    const q = search.toLowerCase()
    const matchSearch = i.name.toLowerCase().includes(q) ||
      (i.description || '').toLowerCase().includes(q) ||
      (i.ip_address || '').includes(q)
    const matchStatus = filterStatus === 'all' || i.status === filterStatus
    return matchSearch && matchStatus
  })

  const upCount   = interfaces.filter(i => i.status === 'up').length
  const downCount = interfaces.filter(i => i.status === 'down').length

  return (
    <div className="flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
            <GitBranch size={15} className="text-ink-secondary" />
          </div>
          <div>
            <h1 className="text-[18px] font-bold text-ink-primary">Interfaces</h1>
            <p className="text-[11px] text-ink-muted mt-0.5">
              {device.name || device.ip_address}
              {interfaces.length > 0 && ` · ${interfaces.length} interfaces · ${upCount} up · ${downCount} down`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            disabled={loading}
            className="p-2 rounded-lg border border-[#252840] text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
            title="Recarregar do banco"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={handleCollect}
            disabled={collecting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold hover:bg-brand-blue-hover disabled:opacity-60 transition-colors"
          >
            {collecting ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
            {collecting ? 'Coletando...' : 'Coletar via SNMP'}
          </button>
        </div>
      </div>

      {/* Collect info */}
      {collectInfo && (
        <div className="bg-green-500/10 border border-green-500/20 rounded-lg px-4 py-2.5 text-[12px] text-green-400 flex items-center gap-3">
          <span>✔ Coleta concluída</span>
          <span className="text-green-300/60">·</span>
          <span>{collectInfo.interface_count} interfaces</span>
          <span className="text-green-300/60">·</span>
          <span>{collectInfo.bgp_peer_count} peers BGP</span>
          <span className="text-green-300/60">·</span>
          <span>{`IPv6 ${collectInfo.ipv6_address_count ?? 0} (${collectInfo.ipv6_source || 'snmp'})`}</span>
          {collectInfo.sys_name && <>
            <span className="text-green-300/60">·</span>
            <span>{collectInfo.sys_name}</span>
          </>}
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg px-4 py-2.5 text-[12px]">
          ⚠ {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Buscar interface, IP, descrição..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px] text-ink-primary placeholder:text-ink-muted outline-none focus:border-brand-blue transition-colors w-64"
        />
        {['all', 'up', 'down'].map(s => (
          <button
            key={s}
            onClick={() => setFilterStatus(s)}
            className={[
              'px-2.5 py-1 rounded-md border text-[11px] font-medium transition-all',
              filterStatus === s
                ? 'bg-[#1e2a45] border-brand-blue/40 text-white'
                : 'border-[#252840] text-ink-muted hover:text-ink-secondary hover:bg-[#1e2235]',
            ].join(' ')}
          >
            {s === 'all' ? 'Todas' : s.toUpperCase()}
            {s !== 'all' && (
              <span className={`ml-1 ${s === 'up' ? 'text-green-400' : 'text-red-400'}`}>
                {s === 'up' ? upCount : downCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16 gap-2 text-ink-muted">
          <Loader2 size={15} className="animate-spin" />
          <span className="text-[12px]">Carregando...</span>
        </div>
      ) : interfaces.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-ink-muted">
          <GitBranch size={32} className="opacity-20" />
          <p className="text-[13px] text-ink-secondary">Nenhuma interface encontrada</p>
          <p className="text-[11px]">Clique em "Coletar via SNMP" para descobrir as interfaces</p>
        </div>
      ) : (
        <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1e2235]">
                {['INTERFACE', 'IPV4 (CIDR)', 'IPV6', 'STATUS', 'ADMIN', 'DESCRIÇÃO', 'AÇÃO'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-[10px] font-semibold tracking-wider text-[#4a5568] bg-[#13151f]">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(iface => {
                const st = STATUS_CFG[iface.status] || STATUS_CFG.unknown
                return (
                  <tr key={iface.id} className="border-b border-[#1e2235] last:border-0 hover:bg-[#1a1d2e] transition-colors">
                    <td className="px-4 py-2.5">
                      <span className={`font-mono text-[12px] font-medium ${iface.is_active ? 'text-ink-primary' : 'text-ink-muted line-through'}`}>
                        {iface.name}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      {iface.ipv4_cidr ? (
                        <span className="font-mono text-[11.5px] text-ink-secondary">
                          {iface.ipv4_cidr}
                        </span>
                      ) : (
                        <span className="text-ink-muted text-[11px]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 max-w-[220px]">
                      {iface.ipv6_addresses?.length ? (
                        <div className="flex flex-col gap-0.5">
                          {iface.ipv6_addresses.map(ip6 => (
                            <span key={ip6} className="font-mono text-[11px] text-ink-secondary truncate">
                              {ip6}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-ink-muted text-[11px]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                        <span className={`text-[11.5px] font-medium ${st.text}`}>{st.label}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-[11px] text-ink-muted capitalize">
                        {iface.admin_status || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 max-w-[200px]">
                      <span className="text-[11px] text-ink-muted truncate block">
                        {iface.description || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        {!iface.is_active && (
                          <span className="inline-flex px-2 py-0.5 rounded border text-[10px] font-semibold bg-rose-500/10 text-rose-300 border-rose-500/30">
                            Inativa
                          </span>
                        )}
                        {!iface.is_active && (
                          <button
                            type="button"
                            onClick={() => handleDeactivateInterface(iface)}
                            disabled={deactivatingInterfaceId === iface.id}
                            title="Remover da plataforma"
                            className="inline-flex items-center justify-center w-7 h-7 rounded-lg border border-rose-500/30 text-rose-300 hover:bg-rose-500/10 disabled:opacity-40 transition-colors"
                          >
                            <Trash2 size={13} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
