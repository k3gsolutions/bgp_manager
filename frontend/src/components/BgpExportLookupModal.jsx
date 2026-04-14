import { useState, useRef, useEffect } from 'react'
import {
  X, Search, Loader2, AlertCircle, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Minus,
} from 'lucide-react'
import { devicesApi } from '../api/devices.js'
import { reportBackendLog } from '../utils/reportBackendLog.js'
import { AsPathTokens } from './AsPathTokens.jsx'
import { useLog } from '../context/LogContext.jsx'

// ── helpers ──────────────────────────────────────────────────────────────────

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
      {items.map(c => <li key={c}><Badge color={color}>{c}</Badge></li>)}
    </ul>
  )
}

function AdvertisedPeerRow({ row, remoteAsn, localAsn }) {
  const [open, setOpen] = useState(false)
  const hasDetail = Boolean(row.advertised_attrs)

  let icon, text, cls
  if (row.advertises === true) {
    icon = <CheckCircle2 size={13} />; text = 'Anunciando'; cls = 'text-green-400'
  } else if (row.advertises === false) {
    icon = <XCircle size={13} />; text = 'Não encontrado'; cls = 'text-red-400/80'
  } else {
    icon = <Minus size={13} />; text = 'Inconclusivo'; cls = 'text-ink-muted'
  }

  return (
    <li className="border border-[#1e2235] rounded-lg bg-[#0f111a] overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-2">
          <span className={`flex items-center gap-1 ${cls} text-[11px] font-semibold`}>
            {icon} {text}
          </span>
          <span className="font-mono text-[11px] text-ink-primary">{row.peer_ip}</span>
          {remoteAsn != null && (
            <span className="text-[10px] text-ink-muted font-mono">AS{remoteAsn}</span>
          )}
        </div>
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className="text-[10px] text-brand-blue hover:underline"
          >
            {open ? 'ocultar detail' : 'ver atributos'}
          </button>
        )}
      </div>
      {open && hasDetail && (
        <div className="px-3 pb-2 border-t border-[#1e2235] pt-2 flex flex-col gap-1.5">
          {row.advertised_attrs?.as_path && (
            <div className="text-[11px] text-ink-secondary">
              <span className="text-ink-muted font-sans">AS-Path: </span>
              <AsPathTokens asPath={row.advertised_attrs.as_path} localAsn={localAsn} compact />
              <p className="mt-1 font-mono text-[10px] text-ink-muted/80 break-all">{row.advertised_attrs.as_path}</p>
            </div>
          )}
          {row.advertised_attrs?.communities?.length > 0 && (
            <div>
              <p className="text-[10px] text-ink-muted mb-1">Communities</p>
              <CommunityList items={row.advertised_attrs.communities} />
            </div>
          )}
          {row.advertised_attrs?.ext_communities?.length > 0 && (
            <div>
              <p className="text-[10px] text-ink-muted mb-1">Ext-Communities</p>
              <CommunityList items={row.advertised_attrs.ext_communities} color="blue" />
            </div>
          )}
          {row.excerpt && (
            <details className="mt-1">
              <summary className="text-[10px] text-ink-muted cursor-pointer">Saída raw</summary>
              <pre className="mt-1 max-h-28 overflow-auto text-[10px] font-mono text-ink-muted bg-black/20 p-1.5 rounded">
                {row.excerpt}
              </pre>
            </details>
          )}
        </div>
      )}
    </li>
  )
}

// ── componente principal ──────────────────────────────────────────────────────

export default function BgpExportLookupModal({ device, open, onClose }) {
  const { addLog } = useLog()
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 80)
    }
  }, [open])

  if (!open || !device) return null

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
      const msg = typeof d?.detail === 'string' ? d.detail : err?.message || 'Falha na consulta'
      setError(msg)
      if (d?.log && Array.isArray(d.log)) reportBackendLog(addLog, 'BGP', 'Trilha (erro)', d.log)
      addLog('error', 'BGP', msg)
    } finally {
      setLoading(false)
    }
  }

  function handleKey(e) {
    if (e.key === 'Escape') onClose()
  }

  const r = result
  const originLabel = { igp: 'Local / Redistribute (igp)', egp: 'eBGP (egp)', '?': 'Redistribuído (?)' }[r?.origin] ?? r?.origin ?? '—'
  const opMap = Object.fromEntries((r?.operator_peers || []).map(p => [p.peer_ip, p]))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
      onKeyDown={handleKey}
    >
      <div
        className="w-full max-w-2xl max-h-[92vh] overflow-hidden flex flex-col rounded-xl border border-[#252840] bg-[#13151f] shadow-2xl"
        role="dialog"
        aria-labelledby="bgp-inv-title"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#1e2235] shrink-0">
          <div>
            <h2 id="bgp-inv-title" className="text-[14px] font-bold text-ink-primary leading-snug">
              BGP Prefix Investigation — <span className="text-brand-blue">{label}</span>
            </h2>
            <p className="text-[10px] text-ink-muted mt-0.5">
              IP, CIDR (ex: 200.1.2.0/24) ou ASN (ex: AS64512) · Huawei VRP via SSH
            </p>
          </div>
          <button type="button" onClick={onClose}
            className="p-2 rounded-lg text-ink-muted hover:text-ink-primary hover:bg-[#1e2235]"
            aria-label="Fechar"
          >
            <X size={16} />
          </button>
        </div>

        {/* Search bar */}
        <form onSubmit={handleSearch}
          className="flex gap-2 px-4 py-3 border-b border-[#1e2235] shrink-0 bg-[#161922]"
        >
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="200.100.50.0/24  ou  203.0.113.1  ou  AS64512"
            className="flex-1 bg-[#0f111a] border border-[#252840] rounded-lg px-3 py-2 text-[13px] text-ink-primary placeholder:text-ink-muted outline-none focus:border-brand-blue"
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

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4 text-[12px]">
          {!isHuawei && (
            <div className="flex gap-2 text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <p>Esta função usa <code>display bgp</code> (Huawei VRP). Outros vendors serão suportados futuramente.</p>
            </div>
          )}

          {error && (
            <div className="flex gap-2 text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <p>{error}</p>
            </div>
          )}

          {/* Placeholder antes da busca */}
          {!loading && !result && !error && (
            <div className="flex flex-col items-center justify-center py-10 gap-3 text-ink-muted">
              <Search size={28} className="opacity-20" />
              <p className="text-[12px] text-ink-muted">
                Digite um prefixo ou ASN e clique em <strong className="text-ink-secondary">Pesquisar</strong>
              </p>
              <ul className="text-[11px] space-y-0.5 text-left list-disc list-inside text-ink-muted/70">
                <li>IP/CIDR → consulta tabela BGP, extrai AS-Path, Origin, Communities</li>
                <li>ASN → encontra prefixos originados por aquele AS</li>
                <li>Verifica anúncio para peers operadora (banco)</li>
              </ul>
            </div>
          )}

          {loading && (
            <div className="flex flex-col items-center justify-center py-10 gap-3 text-ink-muted">
              <Loader2 size={24} className="animate-spin opacity-50" />
              <p className="text-[12px]">Conectando ao equipamento via SSH…</p>
              <p className="text-[11px] text-ink-muted/60">
                display bgp routing-table · peer verbose · advertised-routes
              </p>
            </div>
          )}

          {/* ── Resultado ──────────────────────────────────────────────── */}
          {r && !loading && (
            <div className="flex flex-col gap-4">

              {/* 1. Status geral */}
              <div className="flex flex-wrap gap-2 items-center">
                {r.route_found
                  ? <Badge color="green">✓ Rota encontrada</Badge>
                  : <Badge color="red">✗ Rota não confirmada</Badge>
                }
                {r.prepend_detected && <Badge color="amber">⚠ Prepend detectado</Badge>}
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

              {/* 2. Grid de atributos principais */}
              <div>
                <SectionTitle>Atributos do Best Path</SectionTitle>
                <InfoGrid items={[
                  { label: 'Origin',     value: originLabel,   color: r.origin === 'igp' ? 'text-blue-400' : 'text-slate-300' },
                  { label: 'NextHop',    value: r.nexthop,     color: 'text-ink-secondary font-mono' },
                  { label: 'Local Pref', value: r.local_pref,  color: r.local_pref >= 200 ? 'text-green-400' : 'text-ink-secondary' },
                  { label: 'MED',        value: r.med,         color: 'text-ink-secondary' },
                  { label: 'From Peer',  value: r.from_peer_ip, color: 'text-ink-primary font-mono' },
                  { label: 'AS Local',   value: r.local_asn != null ? `AS${r.local_asn}` : null, color: 'text-ink-muted' },
                ]} />
              </div>

              {/* 3. AS-Path */}
              {r.as_path && (
                <div>
                  <SectionTitle>AS-Path {r.prepend_detected && <span className="text-amber-400 normal-case"> (prepend!)</span>}</SectionTitle>
                  <div className="bg-[#0f111a] border border-[#1e2235] rounded-lg px-3 py-2">
                    <AsPathTokens asPath={r.as_path} localAsn={r.local_asn} />
                    <p className="mt-2 font-mono text-[10px] text-ink-muted/80 break-all border-t border-[#252840] pt-2">
                      {r.as_path}
                    </p>
                  </div>
                </div>
              )}

              {/* 4. Peer origem */}
              {r.from_peer && Object.keys(r.from_peer).length > 0 && (
                <Collapsible title={`Peer origem: ${r.from_peer_ip || '—'}`} defaultOpen>
                  <div className="flex flex-col gap-1.5 text-[11px]">
                    {r.from_peer.remote_asn && <p><span className="text-ink-muted">ASN: </span><span className="font-mono text-ink-secondary">AS{r.from_peer.remote_asn}</span></p>}
                    {r.from_peer.description && <p><span className="text-ink-muted">Descrição: </span><span className="text-ink-primary">{r.from_peer.description}</span></p>}
                    {r.from_peer.peer_type && <p><span className="text-ink-muted">Tipo: </span><span className="text-ink-secondary">{r.from_peer.peer_type}</span></p>}
                    {r.from_peer.route_policy_import && <p><span className="text-ink-muted">Route-Policy Import: </span><span className="font-mono text-blue-300">{r.from_peer.route_policy_import}</span></p>}
                    {r.from_peer.route_policy_export && <p><span className="text-ink-muted">Route-Policy Export: </span><span className="font-mono text-purple-300">{r.from_peer.route_policy_export}</span></p>}
                  </div>
                </Collapsible>
              )}

              {/* 5. Communities */}
              <div>
                <SectionTitle>Communities</SectionTitle>
                <div className="flex flex-col gap-2">
                  <div>
                    <p className="text-[10px] text-ink-muted mb-1">Standard <span className="text-[9px]">(ASN:VALUE)</span></p>
                    <CommunityList items={r.communities} color="purple" />
                  </div>
                  {(r.ext_communities?.length > 0) && (
                    <div>
                      <p className="text-[10px] text-ink-muted mb-1">Extended</p>
                      <CommunityList items={r.ext_communities} color="blue" />
                    </div>
                  )}
                  {(r.large_communities?.length > 0) && (
                    <div>
                      <p className="text-[10px] text-ink-muted mb-1">Large-Community</p>
                      <CommunityList items={r.large_communities} color="slate" />
                    </div>
                  )}
                </div>
              </div>

              {/* 6. Exportação por operadora */}
              <div>
                <SectionTitle>
                  Exportação para operadoras
                  {r.operator_peers?.length > 0 && (
                    <span className="ml-1 text-[9px] font-normal text-ink-muted normal-case">
                      ({r.operator_peers.length} peer{r.operator_peers.length !== 1 ? 's' : ''} cadastrado{r.operator_peers.length !== 1 ? 's' : ''})
                    </span>
                  )}
                </SectionTitle>
                {r.operator_peers?.length === 0 && (
                  <p className="text-[11px] text-ink-muted">
                    Nenhum peer classificado como operadora no banco — classifique na aba BGP.
                  </p>
                )}
                {r.advertised_to?.length > 0 && (
                  <ul className="flex flex-col gap-1.5">
                    {r.advertised_to.map(row => (
                      <AdvertisedPeerRow
                        key={row.peer_ip}
                        row={row}
                        remoteAsn={opMap[row.peer_ip]?.remote_asn}
                        localAsn={r.local_asn}
                      />
                    ))}
                  </ul>
                )}
                {r.advertised_to?.length === 0 && r.operator_peers?.length > 0 && (
                  <p className="text-[11px] text-ink-muted">Nenhum peer verificado (rota não encontrada ou query por ASN).</p>
                )}
              </div>

              {/* 7. Prefixos encontrados (query ASN) */}
              {r.prefixes_found?.length > 0 && (
                <div>
                  <SectionTitle>Prefixos do AS{r.query.replace(/[^0-9]/g,'')}</SectionTitle>
                  <div className="flex flex-wrap gap-1.5">
                    {r.prefixes_found.map(p => <Badge key={p} color="slate">{p}</Badge>)}
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
      </div>
    </div>
  )
}
