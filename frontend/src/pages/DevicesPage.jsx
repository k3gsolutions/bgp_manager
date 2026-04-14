import { useState, useEffect, useCallback } from 'react'
import {
  Plus, Upload, Search, Server, Wifi,
  Pencil, Trash2, Link2, Loader2, RefreshCw,
  ChevronDown, Filter, SquareTerminal,
} from 'lucide-react'
import { formatAxiosError } from '../api/client.js'
import { devicesApi } from '../api/devices.js'
import { companiesApi } from '../api/companies.js'
import DeviceModal from '../components/DeviceModal.jsx'
import DeviceImportModal from '../components/DeviceImportModal.jsx'
import { useLog } from '../context/LogContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { reportBackendLog } from '../utils/reportBackendLog.js'

// ── SSH (único tipo de acesso na grelha) ───────────────────────
const TYPES = {
  ssh: { label: 'SSH', icon: Wifi, bg: 'bg-green-500/10', text: 'text-green-400', border: 'border-green-500/20', dot: '>_' },
}

const ROW_ICON_BG = {
  ssh: 'bg-green-500/10  border-green-500/20  text-green-400',
}

function TypeBadge({ type }) {
  const cfg = TYPES[type] || TYPES.ssh
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] font-semibold ${cfg.bg} ${cfg.text} ${cfg.border}`}>
      <Icon size={10} />
      {cfg.label}
    </span>
  )
}

export default function DevicesPage({ onDeviceCountChange, showModal, onModalClose }) {
  const { addLog } = useLog()
  const { hasPermission, me } = useAuth()
  const canCreateDevice = hasPermission('devices.create')
  const isSuperadmin = (me?.role || '').toLowerCase() === 'superadmin'
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [modal, setModal] = useState(null)
  const [connectingId, setConnectingId] = useState(null)
  const [connStatus, setConnStatus] = useState({})
  const [deletingId, setDeletingId] = useState(null)
  const [vrpingId, setVrpingId] = useState(null)
  /** Antes da coleta SSH VRP: apaga do banco apenas linhas BGP com is_active=false, depois reimporta (incl. VRFs). */
  const [purgeInactiveBgpBeforeVrp, setPurgeInactiveBgpBeforeVrp] = useState(false)
  const [companies, setCompanies] = useState([])
  const [importOpen, setImportOpen] = useState(false)

  // Open modal when triggered externally (e.g. from DeviceTree "+" button)
  useEffect(() => {
    if (showModal) setModal('new')
  }, [showModal])

  const fetchDevices = useCallback(async () => {
    try {
      setError(null)
      const data = await devicesApi.list()
      setDevices(data)
      onDeviceCountChange?.(data.length, data)
    } catch (e) {
      const msg = formatAxiosError(e)
      setError(msg)
      addLog('error', 'API', `GET /api/devices/ — ${msg}`)
    } finally {
      setLoading(false)
    }
  }, [onDeviceCountChange, addLog])

  useEffect(() => { fetchDevices() }, [fetchDevices])

  useEffect(() => {
    let cancelled = false
    companiesApi.list().then(c => {
      if (!cancelled) setCompanies(Array.isArray(c) ? c : [])
    }).catch(() => {
      if (!cancelled) setCompanies([])
    })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!isSuperadmin) setPurgeInactiveBgpBeforeVrp(false)
  }, [isSuperadmin])

  async function handleSave(payload) {
    try {
      if (modal === 'new') {
        await devicesApi.create(payload)
        addLog('success', 'API', `Equipamento criado: ${payload.ip_address}`)
      } else {
        await devicesApi.update(modal.id, payload)
        addLog('success', 'API', `Equipamento atualizado: id=${modal.id}`)
      }
      setModal(null)
      onModalClose?.()
      fetchDevices()
    } catch (err) {
      const d = err?.response?.data?.detail
      const msg = typeof d === 'string' ? d : 'Falha ao salvar equipamento'
      addLog('error', 'API', msg, typeof d === 'string' ? null : JSON.stringify(d))
      throw err
    }
  }

  async function handleDelete(device) {
    if (!confirm(`Remover ${device.name || device.ip_address}?`)) return
    setDeletingId(device.id)
    try {
      await devicesApi.remove(device.id)
      addLog('info', 'API', `Equipamento removido: ${device.name || device.ip_address} (id=${device.id})`)
      const updated = devices.filter(x => x.id !== device.id)
      setDevices(updated)
      onDeviceCountChange?.(updated.length, updated)
    } finally {
      setDeletingId(null)
    }
  }

  async function handleConnect(device) {
    setConnectingId(device.id)
    const label = device.name || device.ip_address
    addLog('info', 'SSH', `Iniciando teste SSH: ${label} (${device.ip_address}:${device.ssh_port})`)
    try {
      const r = await devicesApi.testConnection(device.id)
      reportBackendLog(addLog, 'SSH', `Trilha backend — ${label}`, r.log)
      addLog(r.success ? 'success' : 'error', 'SSH', r.message)
      if (r.success && r.snmp?.ok && r.snmp?.vrfs?.length) {
        addLog('info', 'SNMP', `VRFs: ${r.snmp.vrfs.join(', ')}`)
      }
      setConnStatus(s => ({ ...s, [device.id]: r.success }))
      if (r.success) await fetchDevices()
    } catch (e) {
      const detail = e?.response?.data?.detail
      addLog('error', 'SSH', 'Falha na requisição de teste SSH', typeof detail === 'string' ? detail : String(e?.message || e))
      setConnStatus(s => ({ ...s, [device.id]: false }))
    } finally {
      setConnectingId(null)
    }
  }

  async function handleVrpCollect(device) {
    if ((device.vendor || '') !== 'Huawei') return
    setVrpingId(device.id)
    const label = device.name || device.ip_address
    const purgeFirst = Boolean(isSuperadmin && purgeInactiveBgpBeforeVrp)
    addLog(
      'info',
      'SSH',
      `Coleta VRP Huawei (display * — netops/NE8000): ${label}${purgeFirst ? ' · removendo inativos do banco antes' : ''}`,
    )
    try {
      const res = await devicesApi.sshCollectHuawei(device.id, { purgeInactiveBgpFirst: purgeFirst })
      reportBackendLog(addLog, 'SSH', `Trilha backend — VRP ${label}`, res.log)
      addLog(
        'success',
        'SSH',
        `VRP OK: ${res.interface_count} interfaces, ${res.bgp_peer_count} peers BGP, ${res.vrf_count} VRFs`,
      )
      await fetchDevices()
    } catch (e) {
      const d = e?.response?.data
      const msg = typeof d?.detail === 'string' ? d.detail : e?.message || 'Falha na coleta VRP'
      addLog('error', 'SSH', msg)
      if (d?.log && Array.isArray(d.log)) reportBackendLog(addLog, 'SSH', 'Trilha (erro)', d.log)
    } finally {
      setVrpingId(null)
    }
  }

  const filtered = devices.filter(d => {
    const q = search.toLowerCase()
    return (
      (d.name || '').toLowerCase().includes(q) ||
      d.ip_address.includes(q) ||
      (d.vendor || '').toLowerCase().includes(q) ||
      (d.model || '').toLowerCase().includes(q) ||
      (d.description || '').toLowerCase().includes(q)
    )
  })

  const activeCount = devices.length

  return (
    <div className="flex flex-col gap-5">
      {/* ── Title + actions ── */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
            <Server size={15} className="text-ink-secondary" />
          </div>
          <div>
            <h1 className="text-[20px] font-bold text-ink-primary leading-tight">Equipamentos</h1>
            <p className="text-[11px] text-ink-muted mt-0.5">
              {devices.length} equipamento{devices.length !== 1 ? 's' : ''}
              {' · '}{activeCount} ativo{activeCount !== 1 ? 's' : ''}
              {' · '}acesso SSH
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchDevices}
            className="p-2 rounded-lg border border-[#252840] text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
            title="Recarregar"
          >
            <RefreshCw size={13} />
          </button>
          {canCreateDevice && (
            <button
              type="button"
              onClick={() => setImportOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary text-[12px] font-medium hover:bg-[#1e2235] transition-colors"
              title="Importar vários equipamentos (CSV ou XML)"
            >
              <Upload size={12} />
              Importar CSV / XML
            </button>
          )}
          <button
            onClick={() => setModal('new')}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold hover:bg-brand-blue-hover transition-colors"
          >
            <Plus size={13} />
            Novo Equipamento
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center justify-between bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg px-4 py-2.5 text-[12px]">
          <span>⚠ {error}</span>
          <button onClick={fetchDevices} className="text-[11px] border border-red-500/30 rounded px-2 py-1 hover:bg-red-500/10 transition-colors">Tentar novamente</button>
        </div>
      )}

      {/* ── Search + group filter ── */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input
            type="text"
            placeholder="Buscar por nome, IP ou marca..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full bg-[#161922] border border-[#252840] rounded-lg pl-8 pr-3 py-1.5 text-[12px] text-ink-primary placeholder:text-ink-muted outline-none focus:border-brand-blue transition-colors"
          />
        </div>
        <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary text-[12px] hover:bg-[#1e2235] transition-colors">
          <Filter size={11} />
          Todos os grupos
          <ChevronDown size={11} />
        </button>
      </div>

      {isSuperadmin && (
        <label className="flex items-start gap-2 max-w-3xl text-[11px] text-ink-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={purgeInactiveBgpBeforeVrp}
            onChange={e => setPurgeInactiveBgpBeforeVrp(e.target.checked)}
            className="mt-0.5 rounded border-[#252840] bg-[#13151f] text-brand-blue"
          />
          <span>
            <span className="text-amber-400/90 font-semibold">Superadmin: </span>
            Na <strong className="text-ink-secondary">coleta VRP</strong> (ícone terminal em equipamentos Huawei): apagar do banco apenas peers BGP
            {' '}<span className="text-ink-secondary">inativos</span>, depois reimportar sessões (instância principal + VPN-instances / VRFs).
          </span>
        </label>
      )}

      {/* ── Data Grid ── */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-ink-muted gap-2">
          <Loader2 size={15} className="animate-spin" />
          <span className="text-[12px]">Carregando...</span>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3 text-ink-muted">
          <Server size={36} className="opacity-20" />
          <p className="text-[13px] text-ink-secondary font-medium">
            {search ? 'Nenhum resultado' : 'Nenhum equipamento cadastrado'}
          </p>
          {!search && (
            <button onClick={() => setModal('new')} className="text-[12px] text-brand-blue hover:underline">
              Adicionar primeiro equipamento
            </button>
          )}
        </div>
      ) : (
        <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[#1e2235]">
                {['EQUIPAMENTO', 'TIPO', 'ENDEREÇO', 'MARCA', 'EMPRESA', 'STATUS', 'AÇÕES'].map(h => (
                  <th key={h} className="text-left px-4 py-2.5 text-[10px] font-semibold tracking-wider text-[#4a5568] bg-[#13151f]">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((device, idx) => {
                const type = 'ssh'
                const TypeIcon = TYPES[type]?.icon || Server
                const iconCls = ROW_ICON_BG[type] || ROW_ICON_BG.ssh
                const isConn = connectingId === device.id
                const isVrp = vrpingId === device.id
                const isDel = deletingId === device.id
                const connOk = connStatus[device.id]

                return (
                  <tr
                    key={device.id}
                    className={`border-b border-[#1e2235] last:border-0 hover:bg-[#1a1d2e] transition-colors ${idx % 2 === 1 ? '' : ''}`}
                  >
                    {/* EQUIPAMENTO */}
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <div className={`w-7 h-7 rounded-lg border flex items-center justify-center shrink-0 ${iconCls}`}>
                          <TypeIcon size={12} />
                        </div>
                        <div className="min-w-0">
                          <p className="text-[12.5px] font-semibold text-ink-primary leading-snug truncate max-w-[200px]">
                            {device.name || device.ip_address}
                          </p>
                          <p className="text-[10.5px] text-ink-muted leading-none mt-0.5">
                            {device.description || device.vendor || 'Dispositivo'}
                          </p>
                        </div>
                      </div>
                    </td>

                    {/* TIPO */}
                    <td className="px-4 py-2.5">
                      <TypeBadge type={type} />
                    </td>

                    {/* ENDEREÇO */}
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-[11.5px] text-ink-secondary">
                        {device.ip_address}:{device.ssh_port}
                      </span>
                    </td>

                    {/* MARCA */}
                    <td className="px-4 py-2.5">
                      <span className="text-[12px] text-ink-muted">
                        {device.vendor && device.model ? `${device.vendor} ${device.model}` : device.vendor || '—'}
                      </span>
                    </td>

                    {/* EMPRESA */}
                    <td className="px-4 py-2.5">
                      <span className="px-2 py-0.5 rounded bg-[#1e2235] border border-[#252840] text-ink-muted text-[10.5px]">
                        {device.company_name || `id ${device.company_id ?? '—'}`}
                      </span>
                    </td>

                    {/* STATUS */}
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1.5">
                        <span className={[
                          'w-1.5 h-1.5 rounded-full',
                          connStatus[device.id] === true
                            ? 'bg-green-400 shadow-[0_0_4px_rgba(74,222,128,0.6)]'
                            : connStatus[device.id] === false
                            ? 'bg-red-400'
                            : 'bg-green-400 shadow-[0_0_4px_rgba(74,222,128,0.5)]',
                        ].join(' ')} />
                        <span className="text-[11.5px] text-green-400 font-medium">
                          {connStatus[device.id] === false ? 'Erro' : 'Ativo'}
                        </span>
                      </div>
                    </td>

                    {/* AÇÕES */}
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => setModal(device)}
                          className="p-1.5 rounded-md text-ink-muted hover:text-brand-blue hover:bg-brand-blue-dim transition-colors"
                          title="Editar"
                        >
                          <Pencil size={12} />
                        </button>
                        <button
                          onClick={() => handleConnect(device)}
                          disabled={isConn}
                          className="p-1.5 rounded-md text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
                          title="Testar conexão SSH"
                        >
                          {isConn
                            ? <Loader2 size={12} className="animate-spin" />
                            : <Link2 size={12} />}
                        </button>
                        {device.vendor === 'Huawei' && (
                          <button
                            onClick={() => handleVrpCollect(device)}
                            disabled={isVrp}
                            className="p-1.5 rounded-md text-ink-muted hover:text-amber-400 hover:bg-amber-500/10 transition-colors"
                            title="Coleta inventário VRP (SSH — display interface, BGP, VRF...)"
                          >
                            {isVrp
                              ? <Loader2 size={12} className="animate-spin" />
                              : <SquareTerminal size={12} />}
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(device)}
                          disabled={isDel}
                          className="p-1.5 rounded-md text-ink-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Remover"
                        >
                          {isDel
                            ? <Loader2 size={12} className="animate-spin" />
                            : <Trash2 size={12} />}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {modal && (
        <DeviceModal
          device={modal === 'new' ? null : modal}
          companies={companies}
          onSave={handleSave}
          onClose={() => { setModal(null); onModalClose?.() }}
        />
      )}

      {importOpen && (
        <DeviceImportModal
          companies={companies}
          onClose={() => setImportOpen(false)}
          onImported={res => {
            addLog(
              'success',
              'API',
              `Importação em lote: ${res.created?.length ?? 0} criado(s), ${res.failed?.length ?? 0} falha(s)`,
            )
            fetchDevices()
          }}
        />
      )}
    </div>
  )
}
