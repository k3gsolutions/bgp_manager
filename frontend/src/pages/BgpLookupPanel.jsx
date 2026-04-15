import { useState, useRef, useEffect } from 'react'
import {
  Search, Loader2, AlertCircle, ChevronDown, ChevronRight, Pencil, RefreshCw,
} from 'lucide-react'
import { formatAxiosError } from '../api/client.js'
import { devicesApi } from '../api/devices.js'
import { reportBackendLog } from '../utils/reportBackendLog.js'
import { AsPathTokens } from '../components/AsPathTokens.jsx'
import { useLog } from '../context/LogContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'

// ── helpers ───────────────────────────────────────────────────────────────────

function Badge({ children, color = 'gray' }) {
  const map = {
    green:  'bg-green-500/10  border-green-500/25  text-green-400',
    red:    'bg-red-500/10    border-red-500/25    text-red-400',
    amber:  'bg-amber-500/10  border-amber-500/25  text-amber-400',
    blue:   'bg-blue-500/10   border-blue-500/25   text-blue-400',
    purple: 'bg-purple-500/10 border-purple-500/25 text-purple-300',
    slate:  'bg-slate-500/10  border-slate-500/20  text-slate-300',
    gray:   'bg-[#1e2235]     border-[#252840]     text-ink-muted',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-semibold font-mono ${map[color]}`}>
      {children}
    </span>
  )
}

function SectionTitle({ children }) {
  return (
    <p className="text-[10px] uppercase tracking-wider font-bold text-[#4a5568] mb-1.5">
      {children}
    </p>
  )
}

function InfoGrid({ items }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
      {items.map(({ label, value, color }) => (
        <div key={label} className="bg-[#0f111a] border border-[#1e2235] rounded-lg px-3 py-2">
          <p className="text-[10px] uppercase text-ink-muted font-semibold mb-0.5">{label}</p>
          <p className={`text-[12px] font-medium ${color || 'text-ink-secondary'}`}>{value ?? '—'}</p>
        </div>
      ))}
    </div>
  )
}

function Collapsible({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-[#1e2235] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-[11px] text-ink-secondary hover:bg-[#1a1d2e] transition-colors"
      >
        <span className="font-semibold">{title}</span>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1">
          {children}
        </div>
      )}
    </div>
  )
}

function CommunityList({ items, color = 'purple' }) {
  if (!items?.length) return <p className="text-[11px] text-ink-muted">Nenhuma identificada.</p>
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((c, i) => (
        <li key={`${String(c)}-${i}`}><Badge color={color}>{c}</Badge></li>
      ))}
    </ul>
  )
}

/** FastAPI: `detail` string ou lista de erros de validação. */
function formatApiDetail(detail) {
  if (detail == null || detail === '') return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(x => (typeof x === 'object' && x != null ? (x.msg ?? JSON.stringify(x)) : String(x)))
      .join(' ')
  }
  if (typeof detail === 'object') return detail.msg ?? JSON.stringify(detail)
  return String(detail)
}

/** Evita painel vazio só com chaves vazias vindas do SSH/DB. */
function hasFromPeerDetails(fp) {
  if (!fp || typeof fp !== 'object') return false
  if (fp.remote_asn != null && String(fp.remote_asn).trim() !== '') return true
  for (const k of ['description', 'peer_type', 'route_policy_import', 'route_policy_export', 'vrf_name']) {
    if (String(fp[k] ?? '').trim()) return true
  }
  if (String(fp.raw ?? '').trim().length > 80) return true
  return false
}

/** Rótulo alinhado ao painel BGP: Principal vs VRF nomeada. */
function vrfContextLabel(vrf) {
  const v = (vrf ?? '').toString().trim()
  return v ? `VRF: ${v}` : 'Principal'
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Nome para "Exportação por peer": descrição do BGP (backend); evita duplicar AS7738- no prefixo. */
function formatExportAsName(row, fallbackAsn) {
  const asn = String(row.remote_asn ?? fallbackAsn ?? '—')
  let tail = String(row.peer_name || '').trim()
  if (!tail) return `AS${asn}-—`
  if (tail === row.peer_ip) return `AS${asn}-${row.peer_ip}`
  tail = tail.replace(new RegExp(`^AS${escapeRegExp(asn)}\\s*[-_]\\s*`, 'i'), '').trim()
  return `AS${asn}-${tail}`
}

// ── painel principal ──────────────────────────────────────────────────────────

export default function BgpLookupPanel({ device }) {
  const { addLog } = useLog()
  const { hasPermission } = useAuth()
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [opPrefLoading, setOpPrefLoading] = useState(false)
  const [opPrefError, setOpPrefError] = useState(null)
  const [opPrefData, setOpPrefData] = useState(null)
  const [editRow, setEditRow] = useState(null)
  const [editValue, setEditValue] = useState('')
  const [confirmStep, setConfirmStep] = useState(false)
  const [impactAware, setImpactAware] = useState(false)
  const [applyLoading, setApplyLoading] = useState(false)
  const [applyError, setApplyError] = useState(null)
  const [exportRoleFilter, setExportRoleFilter] = useState('all')
  const inputRef = useRef(null)
  const canEditLocalPref = hasPermission('devices.edit')

  async function refreshOperatorLocalPref(forceRefresh = false) {
    setOpPrefLoading(true)
    setOpPrefError(null)
    if (forceRefresh) {
      addLog('info', 'BGP', `LocalPref: coleta forçada no dispositivo ${label}...`)
    }
    try {
      const data = await devicesApi.bgpOperatorLocalPref(device.id, { forceRefresh })
      setOpPrefData(data)
    } catch (err) {
      const d = err?.response?.data
      const msg = formatApiDetail(d?.detail) || formatAxiosError(err) || 'Falha ao carregar LocalPref'
      setOpPrefError(msg)
    } finally {
      setOpPrefLoading(false)
    }
  }

  // Foca o campo ao montar ou trocar de dispositivo
  useEffect(() => {
    setQuery('')
    setResult(null)
    setError(null)
    setExportRoleFilter('all')
    setTimeout(() => inputRef.current?.focus(), 80)
  }, [device?.id])

  useEffect(() => {
    let mounted = true
    async function loadOperatorLocalPref() {
      try {
        const data = await devicesApi.bgpOperatorLocalPref(device.id)
        if (!mounted) return
        setOpPrefData(data)
      } catch (err) {
        if (!mounted) return
        const d = err?.response?.data
        const msg = formatApiDetail(d?.detail) || formatAxiosError(err) || 'Falha ao carregar LocalPref'
        setOpPrefError(msg)
      } finally {
        if (mounted) setOpPrefLoading(false)
      }
    }
    if (device?.id) {
      setOpPrefLoading(true)
      setOpPrefError(null)
      setOpPrefData(null)
      loadOperatorLocalPref()
    }
    return () => { mounted = false }
  }, [device?.id])

  function openLocalPrefEditor(row) {
    setEditRow(row)
    setEditValue(String(row?.local_preference ?? ''))
    setConfirmStep(false)
    setImpactAware(false)
    setApplyError(null)
  }

  function closeLocalPrefEditor() {
    if (applyLoading) return
    setEditRow(null)
    setEditValue('')
    setConfirmStep(false)
    setImpactAware(false)
    setApplyError(null)
  }

  async function confirmApplyLocalPref() {
    const next = Number.parseInt(String(editValue || '').trim(), 10)
    if (!Number.isFinite(next) || next < 0) {
      setApplyError('Informe um LocalPref válido (>= 0).')
      return
    }
    if (!impactAware) {
      setApplyError('Marque a ciência de impacto para continuar.')
      return
    }
    setApplyLoading(true)
    setApplyError(null)
    try {
      const applied = await devicesApi.applyBgpOperatorLocalPref(device.id, {
        peer_id: editRow.peer_id,
        new_local_preference: next,
        confirm_impact: true,
      })
      await refreshOperatorLocalPref()
      addLog('success', 'BGP', `LocalPref aplicado em ${editRow.peer_ip}: ${applied?.new_local_preference ?? next}`)
      closeLocalPrefEditor()
    } catch (err) {
      const d = err?.response?.data
      const msg = formatApiDetail(d?.detail) || formatAxiosError(err) || 'Falha ao aplicar LocalPref'
      setApplyError(msg)
      addLog('error', 'BGP', msg)
    } finally {
      setApplyLoading(false)
    }
  }

  if (!device) return null

  const label = device.name || device.ip_address
  const isHuawei = (device.vendor || '').toLowerCase() === 'huawei'

  async function handleSearch(e) {
    e?.preventDefault?.()
    const q = query.trim()
    if (!q || !isHuawei) return
    setLoading(true)
    setError(null)
    setResult(null)
    addLog('info', 'BGP', `Prefix investigation: ${label} — ${q}`)
    try {
      const data = await devicesApi.bgpExportLookup(device.id, { query: q })
      reportBackendLog(addLog, 'BGP', `Trilha SSH — investigation ${label}`, data.log || [])
      setResult(data)
      addLog(
        data.route_found ? 'success' : 'info',
        'BGP',
        data.route_found
          ? `Rota encontrada · prepend: ${data.prepend_detected ? 'sim' : 'não'}`
          : 'Rota não confirmada — veja saída raw / log',
      )
    } catch (err) {
      const d = err?.response?.data
      const msg = formatApiDetail(d?.detail) || formatAxiosError(err) || 'Falha na consulta'
      setError(msg)
      if (d?.log && Array.isArray(d.log)) reportBackendLog(addLog, 'BGP', 'Trilha (erro)', d.log)
      addLog('error', 'BGP', msg)
    } finally {
      setLoading(false)
    }
  }

  const r = result
  const originLabel = { igp: 'Local / Redistribute (igp)', egp: 'eBGP (egp)', '?': 'Redistribuído (?)' }[r?.origin] ?? r?.origin ?? '—'
  const opsByIp = {}
  for (const p of r?.operator_peers || []) {
    const ip = p?.peer_ip
    if (!ip) continue
    if (!opsByIp[ip]) opsByIp[ip] = []
    opsByIp[ip].push(p)
  }
  const pickOp = pip => {
    const lst = opsByIp[pip] || []
    if (!lst.length) return null
    const pr = lst.filter(x => !(x.vrf_name || '').toString().trim())
    if (pr.length) return pr[0]
    return lst[0]
  }
  const exportedClassified = (r?.advertised_to || []).filter(Boolean)
  const exportedFiltered = exportedClassified.filter(x => exportRoleFilter === 'all' || x.role === exportRoleFilter)
  const roleCounts = {
    provider: exportedClassified.filter(x => x.role === 'provider').length,
    ix: exportedClassified.filter(x => x.role === 'ix').length,
    customer: exportedClassified.filter(x => x.role === 'customer').length,
    cdn: exportedClassified.filter(x => x.role === 'cdn').length,
    unknown: exportedClassified.filter(x => x.role === 'unknown').length,
  }
  const prefixesAsnLabel = r?.prefixes_found?.length
    ? (() => {
        const asn = String(r.query ?? '').replace(/\D/g, '')
        return asn ? `Prefixos do AS${asn}` : 'Prefixos encontrados'
      })()
    : ''

  return (
    <div className="flex flex-col gap-0 max-w-3xl mx-auto">

      {/* Cabeçalho */}
      <div className="mb-4">
        <h2 className="text-[15px] font-bold text-ink-primary leading-snug">
          BGP Prefix Investigation —{' '}
          <span className="text-brand-blue">{label}</span>
        </h2>
        <p className="text-[11px] text-ink-muted mt-0.5">
          IP, CIDR (ex: 200.1.2.0/24) ou ASN (ex: AS64512) · Huawei VRP via SSH
        </p>
      </div>

      {/* Quadro LocalPref por Operadora */}
      <div className="mb-4 bg-[#13151f] border border-[#1e2235] rounded-xl p-3">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div className="flex items-center gap-2">
            <h3 className="text-[12px] font-semibold text-ink-secondary">
              Operadoras × Local Preference
            </h3>
            <button
              type="button"
              onClick={() => refreshOperatorLocalPref(true)}
              disabled={opPrefLoading}
              title="Atualizar LocalPref"
              aria-label="Atualizar LocalPref"
              className="p-1 rounded border border-[#2c3250] text-ink-secondary hover:bg-[#1a1d2e] disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {opPrefLoading
                ? <RefreshCw size={12} className="animate-spin" />
                : <RefreshCw size={12} />
              }
            </button>
          </div>
          {opPrefData?.collected_at && (
            <span className="text-[10px] text-ink-muted">
              Última atualização em: {new Date(opPrefData.collected_at).toLocaleString()}
            </span>
          )}
        </div>
        {opPrefLoading && (
          <div className="flex items-center gap-2 text-[11px] text-ink-muted py-2">
            <Loader2 size={12} className="animate-spin" />
            Carregando LocalPref das Operadoras…
          </div>
        )}
        {!opPrefLoading && opPrefError && (
          <p className="text-[11px] text-red-300">{opPrefError}</p>
        )}
        {!opPrefLoading && !opPrefError && (!opPrefData?.items || opPrefData.items.length === 0) && (
          <p className="text-[11px] text-ink-muted">Sem Operadoras com dados de LocalPref no backup.</p>
        )}
        {!opPrefLoading && !opPrefError && opPrefData?.items?.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="border-b border-[#252840] text-ink-muted">
                  <th className="text-left py-1.5 pr-2">Operadora</th>
                  <th className="text-left py-1.5 pr-2">Peer</th>
                  <th className="text-left py-1.5 pr-2">VRF</th>
                  <th className="text-left py-1.5 pr-2">Policy Import</th>
                  <th className="text-right py-1.5">LocalPref</th>
                  <th className="text-right py-1.5">Ações</th>
                </tr>
              </thead>
              <tbody>
                {opPrefData.items.map((row, idx) => (
                  <tr key={`${row.peer_id}-${idx}`} className="border-b border-[#1e2235] last:border-0">
                    <td className="py-1.5 pr-2 text-ink-primary">{row.peer_name || '—'}</td>
                    <td className="py-1.5 pr-2 font-mono text-ink-secondary">{row.peer_ip}</td>
                    <td className="py-1.5 pr-2 text-ink-muted">{vrfContextLabel(row.vrf_name)}</td>
                    <td className="py-1.5 pr-2 font-mono text-blue-300">{row.route_policy_import || '—'}</td>
                    <td className="py-1.5 text-right font-mono text-ink-primary">{row.local_preference ?? '—'}</td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        disabled={!canEditLocalPref || !row.route_policy_import}
                        onClick={() => openLocalPrefEditor(row)}
                        title="Editar LocalPref"
                        aria-label="Editar LocalPref"
                        className="px-2 py-1 rounded border border-[#2c3250] text-ink-secondary hover:bg-[#1a1d2e] disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        <Pencil size={12} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editRow && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center px-3">
          <div className="w-full max-w-lg bg-[#13151f] border border-[#252840] rounded-xl p-4">
            <h4 className="text-[13px] font-bold text-ink-primary mb-1">
              Editar Local Preference — {editRow.peer_name || editRow.peer_ip}
            </h4>
            <p className="text-[11px] text-ink-muted mb-3">
              Peer {editRow.peer_ip} · Policy {editRow.route_policy_import} · node 3010
            </p>
            <div className="mb-3">
              <label className="text-[11px] text-ink-muted block mb-1">Novo LocalPref</label>
              <input
                type="number"
                min="0"
                value={editValue}
                onChange={e => setEditValue(e.target.value)}
                disabled={applyLoading}
                className="w-full bg-[#0f111a] border border-[#252840] rounded-lg px-3 py-2 text-[13px] text-ink-primary outline-none focus:border-brand-blue"
              />
            </div>

            {!confirmStep && (
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={closeLocalPrefEditor}
                  disabled={applyLoading}
                  className="px-3 py-1.5 rounded border border-[#2c3250] text-[11px] text-ink-secondary"
                >
                  Cancelar
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmStep(true)}
                  disabled={applyLoading}
                  className="px-3 py-1.5 rounded bg-brand-blue text-white text-[11px] font-semibold"
                >
                  Aplicar
                </button>
              </div>
            )}

            {confirmStep && (
              <div className="mt-2 border border-amber-500/30 bg-amber-500/10 rounded-lg p-3">
                <p className="text-[11px] text-amber-200 mb-2">
                  Confirmação final: esta manobra altera o comportamento de roteamento do dispositivo.
                </p>
                <div className="mb-3 bg-[#0f111a] border border-[#252840] rounded p-2">
                  <p className="text-[10px] text-ink-muted mb-1">Comandos que serão enviados:</p>
                  <pre className="text-[10px] text-ink-secondary whitespace-pre-wrap">{[
                    'system-view',
                    `route-policy ${editRow.route_policy_import} permit node 3010`,
                    `apply local-preference ${String(editValue || '').trim() || '<novo_valor>'}`,
                    'quit',
                    'commit',
                    'quit',
                    `display route-policy ${editRow.route_policy_import}`,
                  ].join('\n')}</pre>
                </div>
                <label className="flex items-start gap-2 text-[11px] text-ink-secondary mb-3">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={impactAware}
                    onChange={e => setImpactAware(e.target.checked)}
                    disabled={applyLoading}
                  />
                  Estou ciente do impacto operacional desta alteração.
                </label>
                {applyError && <p className="text-[11px] text-red-300 mb-2">{applyError}</p>}
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setConfirmStep(false)}
                    disabled={applyLoading}
                    className="px-3 py-1.5 rounded border border-[#2c3250] text-[11px] text-ink-secondary"
                  >
                    Voltar
                  </button>
                  <button
                    type="button"
                    onClick={confirmApplyLocalPref}
                    disabled={applyLoading || !impactAware}
                    className="px-3 py-1.5 rounded bg-red-600 text-white text-[11px] font-semibold disabled:opacity-50"
                  >
                    {applyLoading ? 'Aplicando...' : 'Confirmar e aplicar'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Barra de busca */}
      <form
        onSubmit={handleSearch}
        className="flex gap-2 mb-4"
      >
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="200.100.50.0/24  ou  203.0.113.1  ou  AS64512"
          className="flex-1 bg-[#13151f] border border-[#252840] rounded-lg px-3 py-2 text-[13px] text-ink-primary placeholder:text-ink-muted outline-none focus:border-brand-blue"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !query.trim() || !isHuawei}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-blue text-white text-[12px] font-bold hover:bg-brand-blue-hover disabled:opacity-50 transition-colors"
        >
          {loading
            ? <><Loader2 size={14} className="animate-spin" /> Consultando…</>
            : <><Search size={14} /> Pesquisar</>
          }
        </button>
      </form>

      {/* Alertas */}
      {!isHuawei && (
        <div className="flex gap-2 text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2 mb-3 text-[12px]">
          <AlertCircle size={15} className="shrink-0 mt-0.5" />
          <p>Esta função usa <code>display bgp</code> (Huawei VRP). Outros vendors serão suportados futuramente.</p>
        </div>
      )}

      {error && (
        <div className="flex gap-2 text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2 mb-3 text-[12px]">
          <AlertCircle size={15} className="shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {/* Placeholder */}
      {!loading && !result && !error && (
        <div className="flex flex-col items-center justify-center py-14 gap-3 text-ink-muted bg-[#13151f] border border-[#1e2235] rounded-xl">
          <Search size={28} className="opacity-20" />
          <p className="text-[12px] text-ink-muted">
            Digite um prefixo ou ASN e clique em <strong className="text-ink-secondary">Pesquisar</strong>
          </p>
          <ul className="text-[11px] space-y-0.5 text-left list-disc list-inside text-ink-muted/70">
            <li>IP/CIDR → consulta tabela BGP, extrai AS-Path, Origin, Communities</li>
            <li>ASN → encontra prefixos originados por aquele AS</li>
            <li>Cruza "Advertised to such peers" com peers marcados como Operadora/IX</li>
            <li>Consulta advertised-routes por Operadora/IX e extrai AS-Path e prepend</li>
          </ul>
        </div>
      )}

      {loading && (
        <div className="flex flex-col items-center justify-center py-14 gap-3 text-ink-muted bg-[#13151f] border border-[#1e2235] rounded-xl">
          <Loader2 size={24} className="animate-spin opacity-50" />
          <p className="text-[12px]">Conectando ao equipamento via SSH…</p>
          <p className="text-[11px] text-ink-muted/60">
            display bgp routing-table · peer verbose · advertised-routes
          </p>
        </div>
      )}

      {/* ── Resultado ────────────────────────────────────────────────────────── */}
      {r && !loading && (
        <div className="flex flex-col gap-4 text-[12px]">

          {/* 1. Status geral */}
          <div className="flex flex-wrap gap-2 items-center p-3 bg-[#13151f] border border-[#1e2235] rounded-xl">
            {r.route_found
              ? <Badge color="green">✓ Rota encontrada</Badge>
              : <Badge color="red">✗ Rota não confirmada</Badge>
            }
            {Boolean(r.prepend_detected) && <Badge color="amber">⚠ Prepend detectado</Badge>}
            {r.origin && (
              <Badge color={r.origin === 'igp' ? 'blue' : r.origin === 'egp' ? 'slate' : 'gray'}>
                Origin: {r.origin}
              </Badge>
            )}
            {r.local_pref != null && (
              <Badge color={r.local_pref >= 200 ? 'green' : r.local_pref <= 100 ? 'slate' : 'blue'}>
                LocalPref: {r.local_pref}
              </Badge>
            )}
            {r.med != null && <Badge color="gray">MED: {r.med}</Badge>}
          </div>

          {/* 2. Grid de atributos */}
          <div>
            <SectionTitle>Atributos do Best Path</SectionTitle>
            <InfoGrid items={[
              { label: 'Origin',     value: originLabel,    color: r.origin === 'igp' ? 'text-blue-400' : 'text-slate-300' },
              { label: 'NextHop',    value: r.nexthop,      color: 'text-ink-secondary font-mono' },
              { label: 'Local Pref', value: r.local_pref,   color: r.local_pref != null && r.local_pref >= 200 ? 'text-green-400' : 'text-ink-secondary' },
              { label: 'MED',        value: r.med,          color: 'text-ink-secondary' },
              { label: 'From Peer',  value: r.from_peer_ip, color: 'text-ink-primary font-mono' },
              { label: 'AS Local',   value: r.local_asn != null ? `AS${r.local_asn}` : null, color: 'text-ink-muted' },
            ]} />
          </div>

          {/* 3. AS-Path */}
          {r.as_path && (
            <div>
              <SectionTitle>
                AS-Path {Boolean(r.prepend_detected) && <span className="text-amber-400 normal-case"> (prepend!)</span>}
              </SectionTitle>
              <div className="bg-[#0f111a] border border-[#1e2235] rounded-lg px-3 py-2">
                <AsPathTokens asPath={r.as_path} localAsn={r.local_asn} />
                <p className="mt-2 font-mono text-[10px] text-ink-muted/80 break-all border-t border-[#252840] pt-2">
                  {r.as_path}
                </p>
              </div>
            </div>
          )}

          {/* 4. Peer origem */}
          {hasFromPeerDetails(r.from_peer) && (
            <Collapsible title={`Peer origem: ${r.from_peer_ip || '—'}`} defaultOpen>
              <div className="flex flex-col gap-1.5 text-[11px]">
                <p>
                  <span className="text-ink-muted">Instância BGP: </span>
                  <span className="text-ink-secondary font-medium">{vrfContextLabel(r.from_peer.vrf_name)}</span>
                </p>
                {r.from_peer.remote_asn && <p><span className="text-ink-muted">ASN: </span><span className="font-mono text-ink-secondary">AS{r.from_peer.remote_asn}</span></p>}
                {r.from_peer.description && <p><span className="text-ink-muted">Descrição: </span><span className="text-ink-primary">{r.from_peer.description}</span></p>}
                {r.from_peer.peer_type && <p><span className="text-ink-muted">Tipo: </span><span className="text-ink-secondary">{r.from_peer.peer_type}</span></p>}
                {r.from_peer.route_policy_import && <p><span className="text-ink-muted">Route-Policy Import: </span><span className="font-mono text-blue-300">{r.from_peer.route_policy_import}</span></p>}
                {r.from_peer.route_policy_export && <p><span className="text-ink-muted">Route-Policy Export: </span><span className="font-mono text-purple-300">{r.from_peer.route_policy_export}</span></p>}
                {String(r.from_peer.raw ?? '').trim() && (
                  <div className="mt-2 pt-2 border-t border-[#252840]">
                    <p className="text-[10px] text-ink-muted mb-1">Verbose / raw (peer)</p>
                    <pre className="max-h-36 overflow-auto text-[10px] font-mono text-ink-muted whitespace-pre-wrap bg-[#0a0c14] rounded px-2 py-1.5 border border-[#1e2235]">
                      {r.from_peer.raw}
                    </pre>
                  </div>
                )}
              </div>
            </Collapsible>
          )}

          {/* 4.1 Exportação por peer (só fluxo IP/prefixo com lista advertised) */}
          {exportedClassified.length > 0 && (
          <div>
            <SectionTitle>Exportação por peer</SectionTitle>
            <div className="flex items-center gap-1.5 flex-wrap mb-2">
              {[
                ['all', 'Todos'],
                ['provider', 'Operadora'],
                ['ix', 'IX'],
                ['customer', 'Cliente'],
                ['cdn', 'CDN'],
                ['unknown', 'Sem classe'],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setExportRoleFilter(key)}
                  className={[
                    'px-2 py-0.5 rounded border text-[10px] transition-colors',
                    exportRoleFilter === key
                      ? 'bg-[#1e2a45] border-brand-blue/40 text-white'
                      : 'bg-[#13151f] border-[#252840] text-ink-muted hover:text-ink-secondary',
                  ].join(' ')}
                >
                  {label}
                  {key !== 'all' && ` (${roleCounts[key] || 0})`}
                </button>
              ))}
            </div>
            {exportedFiltered.length === 0 ? (
              <p className="text-[11px] text-ink-muted">Nenhum peer na classe selecionada.</p>
            ) : (
              <div className="flex flex-col gap-1">
                {exportedFiltered.map((row, idx) => {
                  const roleLabel = row.role === 'provider'
                    ? 'Operadora'
                    : row.role === 'ix'
                      ? 'IX'
                      : row.role === 'cdn'
                        ? 'CDN'
                        : row.role === 'customer'
                          ? 'Cliente'
                          : 'Sem classe'
                  const asnFallback = row.remote_asn ?? pickOp(row.peer_ip)?.remote_asn
                  const pathKey = (row.advertised_as_path || '').toString().slice(0, 48)
                  return (
                    <div
                      key={`${row.peer_ip}-${row.vrf_name || ''}-${row.role}-${pathKey}-${idx}`}
                      className="inline-flex items-center gap-1 flex-wrap px-2 py-1 rounded border border-[#2c3150] bg-[#1b2033] text-[10px] text-ink-secondary"
                    >
                      <span className="text-ink-muted">Para</span>
                      <span className="font-mono text-ink-primary">{row.peer_ip}</span>
                      <span className="text-ink-muted">·</span>
                      <strong className="text-ink-primary">{roleLabel}</strong>
                      <span className="text-ink-muted">·</span>
                      <span className="text-[10px] text-ink-muted shrink-0" title="Instância BGP (banco / CLI)">
                        {vrfContextLabel(row.vrf_name)}
                      </span>
                      <span className="text-ink-muted">·</span>
                      <span className="font-mono">{formatExportAsName(row, asnFallback)}</span>
                      {row.advertised_as_path && (
                        <>
                          <span className="text-ink-muted">·</span>
                          <span className="text-ink-muted text-[10px] shrink-0">AS-PATH</span>
                          <AsPathTokens asPath={row.advertised_as_path} localAsn={r.local_asn} compact />
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          )}

          {/* 5. Communities */}
          <div>
            <SectionTitle>Communities</SectionTitle>
            <div className="flex flex-col gap-2">
              <div>
                <p className="text-[10px] text-ink-muted mb-1">Standard <span className="text-[9px]">(ASN:VALUE)</span></p>
                <CommunityList items={r.communities} color="purple" />
              </div>
              {r.ext_communities?.length > 0 && (
                <div>
                  <p className="text-[10px] text-ink-muted mb-1">Extended</p>
                  <CommunityList items={r.ext_communities} color="blue" />
                </div>
              )}
              {r.large_communities?.length > 0 && (
                <div>
                  <p className="text-[10px] text-ink-muted mb-1">Large-Community</p>
                  <CommunityList items={r.large_communities} color="slate" />
                </div>
              )}
            </div>
          </div>

          {/* 6. Prefixos encontrados (query ASN) */}
          {r.prefixes_found?.length > 0 && (
            <div>
              <SectionTitle>{prefixesAsnLabel}</SectionTitle>
              <div className="flex flex-wrap gap-1.5">
                {r.prefixes_found.map((p, i) => <Badge key={`${p}-${i}`} color="slate">{p}</Badge>)}
              </div>
            </div>
          )}

          {/* Raw CLI output */}
          {r.raw_output && (
            <Collapsible title="Saída bruta (CLI)">
              <pre className="max-h-52 overflow-auto text-[10px] font-mono text-ink-muted whitespace-pre-wrap">
                {r.raw_output}
              </pre>
            </Collapsible>
          )}
        </div>
      )}
    </div>
  )
}
