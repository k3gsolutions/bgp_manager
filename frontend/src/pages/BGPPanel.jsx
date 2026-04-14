import { useState, useEffect, useCallback, useLayoutEffect } from 'react'
import { Network, Loader2, Zap, RefreshCw, Trash2, Info, ListTree, Inbox } from 'lucide-react'
import { AsPathTokens } from '../components/AsPathTokens.jsx'
import { snmpApi } from '../api/snmp.js'
import { useLog } from '../context/LogContext.jsx'
import { reportBackendLog } from '../utils/reportBackendLog.js'

/** Filtros BGP (busca, estado, papel, iBGP) por device.id — sobrevive à troca de equipamento na sessão */
const bgpPanelFilterByDeviceId = new Map()

const STATE_CFG = {
  established: { bg: 'bg-green-500/10',  text: 'text-green-400',  border: 'border-green-500/20',  label: 'Established' },
  active:      { bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/20', label: 'Active' },
  idle:        { bg: 'bg-gray-500/10',   text: 'text-gray-400',   border: 'border-gray-500/20',   label: 'Idle' },
  connect:     { bg: 'bg-blue-500/10',   text: 'text-blue-400',   border: 'border-blue-500/20',   label: 'Connect' },
  opensent:    { bg: 'bg-purple-500/10', text: 'text-purple-400', border: 'border-purple-500/20', label: 'OpenSent' },
  openconfirm: { bg: 'bg-indigo-500/10', text: 'text-indigo-400', border: 'border-indigo-500/20', label: 'OpenConfirm' },
  unknown:     { bg: 'bg-gray-500/10',   text: 'text-gray-400',   border: 'border-gray-500/20',   label: 'Unknown' },
}

function fmtUptime(secs) {
  if (secs == null) return '—'
  const d = Math.floor(secs / 86400)
  const h = Math.floor((secs % 86400) / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function peerRoleKey(peer) {
  if (peer.is_provider) return 'provider'
  if (peer.is_ix) return 'ix'
  if (peer.is_cdn) return 'cdn'
  return 'customer'
}

function peerRoleLabel(roleKey) {
  return {
    customer: 'Cliente',
    provider: 'Operadora',
    ix: 'IX',
    cdn: 'CDN',
  }[roleKey] || 'Cliente'
}

/** Operadora, IX e CDN: mesmo fluxo SSH advertised-routes. */
function peerShowsAdvertisedRoutes(peer) {
  return Boolean(peer?.is_provider || peer?.is_ix || peer?.is_cdn)
}

function advertisedRoutesModalTitle(peer) {
  if (!peer) return 'Prefixos advertidos (SSH)'
  return `Prefixos advertidos (SSH) — ${peerRoleLabel(peerRoleKey(peer))}`
}

export default function BGPPanel({ device, snmpPollTick = 0 }) {
  const { addLog } = useLog()
  const [peers, setPeers] = useState([])
  const [loading, setLoading] = useState(false)
  const [collecting, setCollecting] = useState(false)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [filterState, setFilterState] = useState('all')
  /** Padrão: só eBGP; marcado inclui iBGP na lista */
  const [includeIbgp, setIncludeIbgp] = useState(false)
  /** Papel: all | customer | provider | ix | cdn */
  const [filterRole, setFilterRole] = useState('all')
  const [roleSavingId, setRoleSavingId] = useState(null)
  const [deactivatingPeerId, setDeactivatingPeerId] = useState(null)
  const [peerInfoOpen, setPeerInfoOpen] = useState(false)
  const [peerInfoPeer, setPeerInfoPeer] = useState(null)
  const [advOpen, setAdvOpen] = useState(false)
  /** 'provider' = advertised-routes (Operadora); 'customer' = received-routes (Cliente) */
  const [advKind, setAdvKind] = useState('provider')
  const [advPeer, setAdvPeer] = useState(null)
  const [advData, setAdvData] = useState(null)
  const [advLoading, setAdvLoading] = useState(false)
  const [advOffset, setAdvOffset] = useState(0)
  /** Rascunho local do papel até clicar em Salvar */
  const [roleDraftByPeerId, setRoleDraftByPeerId] = useState({})

  const load = useCallback(async (opts = {}) => {
    const quiet = opts.quiet === true
    if (!quiet) {
      setLoading(true)
      setError(null)
    }
    try {
      const data = await snmpApi.bgpPeers(device.id)
      setPeers(data)
    } catch (e) {
      if (!quiet) {
        const msg = e?.response?.data?.detail || 'Erro ao carregar peers BGP'
        setError(msg)
        addLog('error', 'BGP', 'GET bgp-peers', msg)
      }
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [device.id, addLog])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (snmpPollTick > 0) load({ quiet: true })
  }, [snmpPollTick, load])

  useLayoutEffect(() => {
    const s = bgpPanelFilterByDeviceId.get(device.id)
    setSearch(s?.search ?? '')
    setFilterState(s?.filterState ?? 'all')
    setFilterRole(s?.filterRole ?? 'all')
    setIncludeIbgp(s?.includeIbgp ?? false)
    setRoleDraftByPeerId({})
  }, [device.id])

  useEffect(() => {
    bgpPanelFilterByDeviceId.set(device.id, { search, filterState, filterRole, includeIbgp })
  }, [device.id, search, filterState, filterRole, includeIbgp])

  async function handleCollect() {
    if (!device.snmp_community) {
      setError('Community SNMP não configurada para este dispositivo')
      return
    }
    setCollecting(true)
    setError(null)
    const label = device.name || device.ip_address
    addLog('info', 'SNMP', `Coleta completa solicitada: ${label}`)
    try {
      const res = await snmpApi.collect(device.id)
      reportBackendLog(addLog, 'SNMP', `Trilha backend — coleta ${label}`, res.log)
      addLog(
        'success',
        'SNMP',
        `Coleta OK: ${res.interface_count} interfaces, ${res.bgp_peer_count} peers BGP, AS local ${res.local_as ?? '—'}`,
      )
      await load()
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Erro na coleta SNMP'
      setError(msg)
      addLog('error', 'SNMP', msg)
    } finally {
      setCollecting(false)
    }
  }

  const ebgpPeers = peers.filter(p => !p.is_ibgp)
  const ibgpPeers = peers.filter(p => p.is_ibgp)

  const sessionPool = peers.filter(p => includeIbgp || !p.is_ibgp)

  const filtered = sessionPool.filter(p => {
    const q = search.toLowerCase()
    const vrfq = (p.vrf_name || '').toLowerCase()
    const matchSearch = p.peer_ip.includes(q) ||
      String(p.remote_asn || '').includes(q) ||
      vrfq.includes(q)
    const matchState = filterState === 'all' || p.status === filterState
    const role = peerRoleKey(p)
    const matchRole =
      filterRole === 'all'
      || filterRole === role
    return matchSearch && matchState && matchRole
  })

  const estCount = peers.filter(p => p.status === 'established').length
  const estCountEbgp = ebgpPeers.filter(p => p.status === 'established').length
  const localAsn = peers.find(p => p.device_local_asn != null)?.device_local_asn ?? device.local_asn

  const rolePoolCounts = {
    customer: sessionPool.filter(p => peerRoleKey(p) === 'customer').length,
    provider: sessionPool.filter(p => peerRoleKey(p) === 'provider').length,
    ix: sessionPool.filter(p => peerRoleKey(p) === 'ix').length,
    cdn: sessionPool.filter(p => peerRoleKey(p) === 'cdn').length,
  }

  function savedPeerRole(peer) {
    return peerRoleKey(peer)
  }

  function displayPeerRole(peer) {
    return roleDraftByPeerId[peer.id] ?? savedPeerRole(peer)
  }

  function isPeerRoleDirty(peer) {
    return displayPeerRole(peer) !== savedPeerRole(peer)
  }

  function handleDraftRoleChange(peer, role) {
    setRoleDraftByPeerId(prev => {
      if (role === savedPeerRole(peer)) {
        const next = { ...prev }
        delete next[peer.id]
        return next
      }
      return { ...prev, [peer.id]: role }
    })
  }

  async function handleSavePeerRole(peer) {
    const role = displayPeerRole(peer)
    if (role === savedPeerRole(peer)) return
    const is_provider = role === 'provider'
    const is_customer = role === 'customer'
    const is_ix = role === 'ix'
    const is_cdn = role === 'cdn'
    setRoleSavingId(peer.id)
    setError(null)
    try {
      await snmpApi.updatePeerRole(device.id, peer.id, { is_customer, is_provider, is_ix, is_cdn })
      addLog(
        'success',
        'BGP',
        `Papel do peer ${peer.peer_ip}: ${peerRoleLabel(role)}`,
      )
      setPeers(prev =>
        prev.map(x =>
          x.id === peer.id ? { ...x, is_customer, is_provider, is_ix, is_cdn } : x
        )
      )
      setRoleDraftByPeerId(prev => {
        const next = { ...prev }
        delete next[peer.id]
        return next
      })
    } catch (e) {
      const d = e?.response?.data?.detail
      const msg = Array.isArray(d)
        ? d.map(x => x.msg || x).join(' ')
        : d || 'Erro ao salvar classificação'
      setError(msg)
    } finally {
      setRoleSavingId(null)
    }
  }

  async function handleRemovePeerFromDb(peer) {
    const vrf = (peer.vrf_name || '').toString().trim()
    const vrfHint = vrf ? ` (VRF: ${vrf})` : ''
    if (
      !window.confirm(
        `Remover permanentemente o peer ${peer.peer_ip}${vrfHint} da base deste equipamento?\n\n` +
          'O registro some da lista; na próxima coleta SNMP/SSH ele pode voltar se a sessão ainda existir no roteador.',
      )
    ) {
      return
    }
    setDeactivatingPeerId(peer.id)
    setError(null)
    try {
      await snmpApi.deletePeer(device.id, peer.id)
      setPeers(prev => prev.filter(x => x.id !== peer.id))
      addLog('warning', 'BGP', `Peer removido da base: ${peer.peer_ip}${vrfHint}`)
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Erro ao remover peer'
      setError(msg)
      addLog('error', 'BGP', msg)
    } finally {
      setDeactivatingPeerId(null)
    }
  }

  function openPeerInfo(peer) {
    setPeerInfoPeer(peer)
    setPeerInfoOpen(true)
  }

  function closePeerInfo() {
    setPeerInfoOpen(false)
    setPeerInfoPeer(null)
  }

  function closeAdvModal() {
    setAdvOpen(false)
    setAdvPeer(null)
    setAdvData(null)
    setAdvOffset(0)
    setAdvKind('provider')
  }

  async function loadPeerRouteList(peer, offset = 0, kind = 'provider') {
    if (kind === 'provider' && !peerShowsAdvertisedRoutes(peer)) return
    if (kind === 'customer' && !peer?.is_customer) return
    setAdvKind(kind)
    setAdvPeer(peer)
    setAdvOpen(true)
    setAdvLoading(true)
    setAdvOffset(offset)
    setAdvData(null)
    const label = device.name || device.ip_address
    const advRoleLabel =
      kind === 'provider' ? peerRoleLabel(peerRoleKey(peer)) : 'Cliente'
    const logLabel =
      kind === 'provider'
        ? `SSH — prefixos advertidos (${advRoleLabel}): ${peer.peer_ip} · ${label}`
        : `SSH — prefixos recebidos (Cliente): ${peer.peer_ip} · ${label}`
    addLog('info', 'BGP', logLabel)
    try {
      const res =
        kind === 'provider'
          ? await snmpApi.bgpProviderAdvertisedRoutes(device.id, { peer_id: peer.id, offset })
          : await snmpApi.bgpCustomerReceivedRoutes(device.id, { peer_id: peer.id, offset })
      setAdvData(res)
      const trailTitle = kind === 'provider' ? `Trilha SSH — advertised ${peer.peer_ip}` : `Trilha SSH — received ${peer.peer_ip}`
      reportBackendLog(addLog, 'BGP', trailTitle, res.log || [])
      if (res.too_many) {
        addLog('warn', 'BGP', res.message || (kind === 'provider' ? 'Muitas rotas advertidas' : 'Muitas rotas recebidas'))
      } else if (res.capped && res.message) {
        addLog('warn', 'BGP', res.message)
        const verb = kind === 'provider' ? 'Advertidos' : 'Recebidos'
        addLog(
          'success',
          'BGP',
          `${verb}: ${res.items?.length ?? 0} prefixo(s) nesta página (listados até ${res.total ?? 0})`,
        )
      } else {
        const verb = kind === 'provider' ? 'Advertidos' : 'Recebidos'
        addLog(
          'success',
          'BGP',
          `${verb}: ${res.items?.length ?? 0} prefixo(s) nesta página (total ${res.total ?? 0})`,
        )
      }
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Falha na consulta SSH'
      setAdvData({ error: true, message: msg, items: [], total: 0, offset: 0, has_more: false, too_many: false })
      addLog('error', 'BGP', msg)
      const d = e?.response?.data
      if (d?.log && Array.isArray(d.log)) reportBackendLog(addLog, 'BGP', 'Trilha (erro)', d.log)
    } finally {
      setAdvLoading(false)
    }
  }

  const isHuawei = (device.vendor || '').toLowerCase() === 'huawei'

  return (
    <div className="flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
            <Network size={15} className="text-ink-secondary" />
          </div>
          <div>
            <h1 className="text-[18px] font-bold text-ink-primary">BGP Peers</h1>
            <p className="text-[11px] text-ink-muted mt-0.5">
              {device.name || device.ip_address}
              {localAsn != null && ` · AS local ${localAsn}`}
              {peers.length > 0 && (
                <>
                  {` · ${ebgpPeers.length} eBGP`}
                  {ibgpPeers.length > 0 && ` · ${ibgpPeers.length} iBGP`}
                  {` · ${estCountEbgp} eBGP est.`}
                  {estCount !== estCountEbgp && ` · ${estCount} est. total`}
                </>
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} disabled={loading}
            className="p-2 rounded-lg border border-[#252840] text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
            title="Recarregar">
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button onClick={handleCollect} disabled={collecting}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold hover:bg-brand-blue-hover disabled:opacity-60 transition-colors">
            {collecting ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
            {collecting ? 'Coletando...' : 'Coletar via SNMP'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg px-4 py-2.5 text-[12px]">
          ⚠ {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            placeholder="Buscar IP, ASN..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px] text-ink-primary placeholder:text-ink-muted outline-none focus:border-brand-blue transition-colors w-52"
          />
          <label className="flex items-center gap-2 cursor-pointer select-none text-[11px] text-ink-secondary">
            <input
              type="checkbox"
              checked={includeIbgp}
              onChange={e => setIncludeIbgp(e.target.checked)}
              className="rounded border-[#252840] bg-[#13151f] text-brand-blue focus:ring-brand-blue/40"
            />
            Incluir iBGP
          </label>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[#4a5568] w-full sm:w-auto sm:mr-1">
            Estado
          </span>
          {['all', 'established', 'active', 'idle'].map(s => (
            <button key={s}
              type="button"
              onClick={() => setFilterState(s)}
              className={[
                'px-2.5 py-1 rounded-md border text-[11px] font-medium transition-all',
                filterState === s
                  ? 'bg-[#1e2a45] border-brand-blue/40 text-white'
                  : 'border-[#252840] text-ink-muted hover:text-ink-secondary hover:bg-[#1e2235]',
              ].join(' ')}
            >
              {s === 'all' ? 'Todos' : s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[#4a5568] w-full sm:w-auto sm:mr-1">
            Papel
          </span>
          {[
            { key: 'all', label: 'Todos' },
            { key: 'customer', label: 'Clientes', count: rolePoolCounts.customer },
            { key: 'provider', label: 'Operadoras', count: rolePoolCounts.provider },
            { key: 'ix', label: 'IX', count: rolePoolCounts.ix },
            { key: 'cdn', label: 'CDN', count: rolePoolCounts.cdn },
          ].map(({ key, label, count }) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilterRole(key)}
              className={[
                'px-2.5 py-1 rounded-md border text-[11px] font-medium transition-all inline-flex items-center gap-1.5',
                filterRole === key
                  ? 'bg-[#1e2a45] border-brand-blue/40 text-white'
                  : 'border-[#252840] text-ink-muted hover:text-ink-secondary hover:bg-[#1e2235]',
              ].join(' ')}
            >
              {label}
              {key !== 'all' && count != null && (
                <span
                  className={[
                    'px-1.5 py-px rounded text-[10px] font-semibold',
                    filterRole === key ? 'bg-brand-blue text-white' : 'bg-[#252840] text-ink-muted',
                  ].join(' ')}
                >
                  {count}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16 gap-2 text-ink-muted">
          <Loader2 size={15} className="animate-spin" />
          <span className="text-[12px]">Carregando...</span>
        </div>
      ) : peers.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-ink-muted">
          <Network size={32} className="opacity-20" />
          <p className="text-[13px] text-ink-secondary">Nenhum peer BGP encontrado</p>
          <p className="text-[11px]">Clique em "Coletar via SNMP" para buscar os peers</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-ink-muted">
          <Network size={32} className="opacity-20" />
          <p className="text-[13px] text-ink-secondary">Nenhum peer corresponde aos filtros</p>
          <p className="text-[11px]">
            Ajuste busca, estado, papel ou marque &quot;Incluir iBGP&quot; se só houver sessões internas.
          </p>
        </div>
      ) : (
        <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1e2235]">
                {['PEER IP', 'NOME', 'ASN REMOTO', 'SESSÃO / VRF', 'ESTADO', 'UPTIME', 'PAPEL'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-[10px] font-semibold tracking-wider text-[#4a5568] bg-[#13151f]">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(peer => {
                const st = STATE_CFG[peer.status] || STATE_CFG.unknown
                return (
                  <tr key={`${peer.id}-${peer.vrf_name || ''}`} className="border-b border-[#1e2235] last:border-0 hover:bg-[#1a1d2e] transition-colors">
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className={`font-mono text-[12px] font-medium ${peer.is_active ? 'text-ink-primary' : 'text-ink-muted line-through'}`}>
                          {peer.peer_ip}
                        </span>
                        <button
                          type="button"
                          title="Informações do peer"
                          onClick={() => openPeerInfo(peer)}
                          className="inline-flex items-center justify-center w-6 h-6 rounded border border-[#2b3046] text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
                        >
                          <Info size={12} />
                        </button>
                        {peerShowsAdvertisedRoutes(peer) && isHuawei && (
                          <button
                            type="button"
                            title="Prefixos advertidos (SSH — Operadora, IX ou CDN)"
                            onClick={() => loadPeerRouteList(peer, 0, 'provider')}
                            disabled={!peer.is_active || (advLoading && advPeer?.id === peer.id)}
                            className="inline-flex items-center justify-center w-6 h-6 rounded border border-[#2b3046] text-ink-muted hover:text-amber-400 hover:bg-amber-500/10 transition-colors disabled:opacity-40"
                          >
                            {advLoading && advPeer?.id === peer.id && advKind === 'provider'
                              ? <Loader2 size={12} className="animate-spin text-amber-400" />
                              : <ListTree size={12} />}
                          </button>
                        )}
                        {peer.is_customer && isHuawei && (
                          <button
                            type="button"
                            title="Prefixos recebidos (SSH — received-routes, só Cliente)"
                            onClick={() => loadPeerRouteList(peer, 0, 'customer')}
                            disabled={!peer.is_active || (advLoading && advPeer?.id === peer.id)}
                            className="inline-flex items-center justify-center w-6 h-6 rounded border border-[#2b3046] text-ink-muted hover:text-purple-400 hover:bg-purple-500/10 transition-colors disabled:opacity-40"
                          >
                            {advLoading && advPeer?.id === peer.id && advKind === 'customer'
                              ? <Loader2 size={12} className="animate-spin text-purple-400" />
                              : <Inbox size={12} />}
                          </button>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-[11.5px] text-ink-secondary truncate block max-w-[260px]">
                        {peer.peer_name || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-[12px] text-ink-secondary">
                        {peer.remote_asn ? `AS${peer.remote_asn}` : '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-col gap-1 items-start">
                        <span
                          className={
                            peer.is_ibgp
                              ? 'inline-flex px-2 py-0.5 rounded border text-[11px] font-semibold bg-amber-500/10 text-amber-400 border-amber-500/25'
                              : 'inline-flex px-2 py-0.5 rounded border text-[11px] font-semibold bg-slate-500/10 text-slate-300 border-slate-500/20'
                          }
                          title={
                            peer.is_ibgp
                              ? 'ASN remoto igual ao AS local (iBGP)'
                              : 'ASN diferente do AS local (eBGP)'
                          }
                        >
                          {peer.is_ibgp ? 'iBGP' : 'eBGP'}
                        </span>
                        <span
                          className="text-[10px] font-medium text-ink-muted max-w-[200px] truncate"
                          title={(peer.vrf_name && String(peer.vrf_name).trim()) ? `VPN-Instance: ${peer.vrf_name}` : 'Instância BGP global (principal)'}
                        >
                          {(peer.vrf_name && String(peer.vrf_name).trim())
                            ? `VRF: ${peer.vrf_name}`
                            : 'Principal'}
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-semibold ${st.bg} ${st.text} ${st.border}`}>
                        {st.label}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-[11.5px] text-ink-muted">
                        {fmtUptime(peer.uptime_secs)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        {!peer.is_active && (
                          <span className="inline-flex px-2 py-0.5 rounded border text-[10px] font-semibold bg-rose-500/10 text-rose-300 border-rose-500/30">
                            Inativo
                          </span>
                        )}
                        <select
                          value={displayPeerRole(peer)}
                          disabled={roleSavingId === peer.id || !peer.is_active}
                          onChange={e => handleDraftRoleChange(peer, e.target.value)}
                          className={[
                            'bg-[#13151f] border rounded-lg px-2 py-1 text-[11px] text-ink-primary outline-none focus:border-brand-blue disabled:opacity-50',
                            isPeerRoleDirty(peer)
                              ? 'border-amber-500/50 ring-1 ring-amber-500/20'
                              : 'border-[#252840]',
                          ].join(' ')}
                        >
                          <option value="customer">Cliente</option>
                          <option value="provider">Operadora</option>
                          <option value="ix">IX</option>
                          <option value="cdn">CDN</option>
                        </select>
                        {isPeerRoleDirty(peer) && (
                          <button
                            type="button"
                            onClick={() => handleSavePeerRole(peer)}
                            disabled={roleSavingId === peer.id}
                            className="px-2.5 py-1 rounded-lg bg-brand-blue text-white text-[11px] font-semibold hover:bg-brand-blue-hover disabled:opacity-50 transition-colors shrink-0"
                          >
                            {roleSavingId === peer.id ? 'Salvando...' : 'Salvar'}
                          </button>
                        )}
                        {!peer.is_active && (
                          <button
                            type="button"
                            onClick={() => handleRemovePeerFromDb(peer)}
                            disabled={deactivatingPeerId === peer.id}
                            title="Remover registro deste equipamento (DELETE no banco)"
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

      {advOpen && advPeer && (
        <div className="fixed inset-0 z-[60] bg-black/60 backdrop-blur-[1px] flex items-center justify-center p-4">
          <div className="w-full max-w-2xl max-h-[90vh] flex flex-col bg-[#11141f] border border-[#2b3046] rounded-xl shadow-xl">
            <div className="px-4 py-3 border-b border-[#1e2235] flex items-center justify-between shrink-0">
              <h3 className="text-[14px] font-bold text-ink-primary">
                {advKind === 'provider'
                  ? advertisedRoutesModalTitle(advPeer)
                  : 'Prefixos recebidos (SSH) — Cliente'}
              </h3>
              <button type="button" onClick={closeAdvModal} className="px-2 py-1 text-[11px] text-ink-muted hover:text-ink-primary">
                Fechar
              </button>
            </div>
            <div className="p-4 space-y-3 overflow-y-auto flex-1 min-h-0">
              <p className="text-[11px] text-ink-muted">
                <span className="font-mono text-ink-secondary">{advPeer.peer_ip}</span>
                {(advPeer.vrf_name || '').toString().trim()
                  ? ` · VRF: ${advPeer.vrf_name}`
                  : ' · Principal'}
              </p>
              {advLoading && (
                <div className="flex items-center gap-2 text-ink-muted text-[12px] py-6">
                  <Loader2 size={14} className="animate-spin" />
                  Consultando equipamento via SSH…
                </div>
              )}
              {!advLoading && advData?.too_many && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-200 px-3 py-2 text-[12px]">
                  {advData.message}
                </div>
              )}
              {!advLoading && advData?.capped && advData?.message && !advData?.too_many && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-100 px-3 py-2 text-[12px] leading-snug">
                  {advData.message}
                </div>
              )}
              {!advLoading && advData?.error && !advData?.too_many && (
                <div className="rounded-lg border border-red-500/30 bg-red-500/10 text-red-300 px-3 py-2 text-[12px]">
                  {advData.message || 'Erro'}
                </div>
              )}
              {!advLoading && advData && !advData.too_many && !advData.error && advData.total === 0 && (
                <p className="text-[12px] text-ink-muted">
                  {advKind === 'provider'
                    ? 'Nenhum prefixo encontrado na saída de advertised-routes.'
                    : 'Nenhum prefixo encontrado na saída de received-routes.'}
                </p>
              )}
              {!advLoading && advData?.items?.length > 0 && (
                <>
                  <p className="text-[12px] text-ink-secondary font-medium">
                    {advKind === 'provider'
                      ? advData.capped && advData.full_total != null
                        ? `Prefixos advertidos (visíveis): ${advData.total} de ${advData.full_total} na tabela do equipamento`
                        : `Total de prefixos advertidos: ${advData.reported_total ?? advData.total}`
                      : advData.capped && advData.full_total != null
                        ? `Prefixos recebidos (visíveis): ${advData.total} de ${advData.full_total} na tabela do equipamento`
                        : `Total de prefixos recebidos: ${advData.reported_total ?? advData.total}`}
                  </p>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-ink-muted">
                    <span>
                      {advData.offset + 1}–{advData.offset + advData.items.length} de {advData.total}
                    </span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={advData.offset <= 0 || advLoading}
                        onClick={() =>
                          loadPeerRouteList(
                            advPeer,
                            Math.max(0, advData.offset - (advData.page_size || 20)),
                            advKind,
                          )}
                        className="px-2 py-1 rounded border border-[#252840] text-ink-secondary hover:bg-[#1e2235] disabled:opacity-40 text-[11px]"
                      >
                        Anterior
                      </button>
                      <button
                        type="button"
                        disabled={!advData.has_more || advLoading}
                        onClick={() =>
                          loadPeerRouteList(advPeer, advData.offset + (advData.page_size || 20), advKind)}
                        className="px-2 py-1 rounded border border-[#252840] text-ink-secondary hover:bg-[#1e2235] disabled:opacity-40 text-[11px]"
                      >
                        Próxima
                      </button>
                    </div>
                  </div>
                  <div className="rounded-md border border-[#252840] bg-[#0d0f16] px-3 py-2 text-[11px] leading-relaxed text-ink-primary space-y-2">
                    {advData.items.map((row, i) => (
                      <div
                        key={`${advData.offset}-${row.prefix}-${i}`}
                        className="flex flex-col gap-1.5 sm:flex-row sm:flex-wrap sm:items-center border-b border-[#1a1d2e] last:border-0 pb-2 last:pb-0"
                      >
                        <span className="font-mono text-ink-primary shrink-0">{row.prefix}</span>
                        <span className="text-[10px] text-ink-muted shrink-0 hidden sm:inline">·</span>
                        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                          <span className="text-[10px] uppercase tracking-wide text-ink-muted shrink-0">AS-PATH</span>
                          <AsPathTokens asPath={row.as_path} localAsn={device.local_asn} compact />
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {peerInfoOpen && peerInfoPeer && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[1px] flex items-center justify-center p-4">
          <div className="w-full max-w-xl bg-[#11141f] border border-[#2b3046] rounded-xl shadow-xl">
            <div className="px-4 py-3 border-b border-[#1e2235] flex items-center justify-between">
              <h3 className="text-[14px] font-bold text-ink-primary">
                {`AS${peerInfoPeer.remote_asn ?? '—'}-${peerInfoPeer.peer_name || peerInfoPeer.peer_ip}`}
              </h3>
              <button
                type="button"
                onClick={closePeerInfo}
                className="px-2 py-1 text-[11px] text-ink-muted hover:text-ink-primary"
              >
                Fechar
              </button>
            </div>
            <div className="p-4 space-y-3">
              <div className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-2">
                <p className="text-[10px] uppercase tracking-wider text-[#4a5568]">Local IP</p>
                <p className="font-mono text-[12px] text-ink-secondary mt-1">{peerInfoPeer.local_addr || '—'}</p>
              </div>
              <div className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-2">
                <p className="text-[10px] uppercase tracking-wider text-[#4a5568]">Advertised-routes</p>
                <p className="font-mono text-[12px] text-blue-400 mt-1">{peerInfoPeer.out_updates ?? '—'}</p>
              </div>
              <div className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-2">
                <p className="text-[10px] uppercase tracking-wider text-[#4a5568]">Received-routes</p>
                <p className="font-mono text-[12px] text-purple-400 mt-1">{peerInfoPeer.in_updates ?? '—'}</p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
