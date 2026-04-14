import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Loader2, RefreshCw, Server, WifiOff, XCircle, Zap } from 'lucide-react'
import { formatAxiosError } from '../api/client.js'
import { devicesApi } from '../api/devices.js'
import { snmpApi } from '../api/snmp.js'
import { useLog } from '../context/LogContext.jsx'

function StatusBadge({ status }) {
  const map = {
    idle: 'bg-[#1e2235] border-[#252840] text-ink-muted',
    loading: 'bg-blue-500/10 border-blue-500/25 text-blue-400',
    ok: 'bg-green-500/10 border-green-500/25 text-green-400',
    error: 'bg-red-500/10 border-red-500/25 text-red-400',
    disabled: 'bg-slate-500/10 border-slate-500/20 text-slate-400',
  }
  const text = {
    idle: 'Não testado',
    loading: 'Verificando...',
    ok: 'OK',
    error: 'Falha',
    disabled: 'Não configurado',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-semibold ${map[status] || map.idle}`}>
      {text[status] || text.idle}
    </span>
  )
}

export default function DashboardPage({ onDeviceCountChange }) {
  const { addLog } = useLog()
  const [devices, setDevices] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [sshStateById, setSshStateById] = useState({})
  const [snmpStateById, setSnmpStateById] = useState({})

  const loadDevices = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await devicesApi.list()
      setDevices(data)
      onDeviceCountChange?.(data.length, data)
    } catch (e) {
      const errText = formatAxiosError(e)
      setError(errText)
      addLog('error', 'DASH', `GET devices — ${errText}`)
    } finally {
      setLoading(false)
    }
  }, [addLog, onDeviceCountChange])

  useEffect(() => {
    loadDevices()
  }, [loadDevices])

  const totals = useMemo(() => {
    const sshOk = Object.values(sshStateById).filter(v => v === 'ok').length
    const snmpOk = Object.values(snmpStateById).filter(v => v === 'ok').length
    return { sshOk, snmpOk }
  }, [sshStateById, snmpStateById])

  async function testSsh(device) {
    setSshStateById(s => ({ ...s, [device.id]: 'loading' }))
    try {
      const res = await devicesApi.testConnection(device.id)
      setSshStateById(s => ({ ...s, [device.id]: res.success ? 'ok' : 'error' }))
      addLog(res.success ? 'success' : 'error', 'SSH', `${device.name || device.ip_address}: ${res.message}`)
    } catch (e) {
      setSshStateById(s => ({ ...s, [device.id]: 'error' }))
      addLog('error', 'SSH', `${device.name || device.ip_address}: ${e?.response?.data?.detail || formatAxiosError(e)}`)
    }
  }

  async function collectSnmp(device) {
    if (!device.snmp_community) {
      setSnmpStateById(s => ({ ...s, [device.id]: 'disabled' }))
      return
    }
    setSnmpStateById(s => ({ ...s, [device.id]: 'loading' }))
    try {
      await snmpApi.collect(device.id)
      setSnmpStateById(s => ({ ...s, [device.id]: 'ok' }))
      addLog('success', 'SNMP', `Coleta concluída: ${device.name || device.ip_address}`)
    } catch (e) {
      setSnmpStateById(s => ({ ...s, [device.id]: 'error' }))
      addLog('error', 'SNMP', `${device.name || device.ip_address}: ${e?.response?.data?.detail || formatAxiosError(e)}`)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-[18px] font-bold text-ink-primary">Dashboard</h1>
          <p className="text-[11px] text-ink-muted mt-0.5">
            {devices.length} dispositivo(s) · SSH OK: {totals.sshOk} · SNMP OK: {totals.snmpOk}
          </p>
        </div>
        <button
          onClick={loadDevices}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary hover:bg-[#1e2235] disabled:opacity-60 text-[12px] font-semibold"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          Atualizar lista
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg px-4 py-2.5 text-[12px]">
          {error}
        </div>
      )}

      <div className="bg-[#161922] border border-[#1e2235] rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[#1e2235]">
              {['DISPOSITIVO', 'IP', 'SSH', 'SNMP', 'AÇÕES'].map(h => (
                <th key={h} className="text-left px-4 py-2.5 text-[10px] font-semibold tracking-wider text-[#4a5568] bg-[#13151f]">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {devices.map(d => {
              const sshState = sshStateById[d.id] || 'idle'
              const snmpState = d.snmp_community ? (snmpStateById[d.id] || 'idle') : 'disabled'
              return (
                <tr key={d.id} className="border-b border-[#1e2235] last:border-0 hover:bg-[#1a1d2e] transition-colors">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Server size={13} className="text-ink-muted" />
                      <span className="text-[12px] text-ink-primary font-medium">{d.name || '-'}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="font-mono text-[11.5px] text-ink-secondary">{d.ip_address}</span>
                  </td>
                  <td className="px-4 py-2.5"><StatusBadge status={sshState} /></td>
                  <td className="px-4 py-2.5"><StatusBadge status={snmpState} /></td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => testSsh(d)}
                        disabled={sshState === 'loading'}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-[#252840] text-[11px] text-ink-secondary hover:bg-[#1e2235] disabled:opacity-60"
                      >
                        {sshState === 'loading' ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={12} />}
                        Testar SSH
                      </button>
                      <button
                        onClick={() => collectSnmp(d)}
                        disabled={snmpState === 'loading' || !d.snmp_community}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-brand-blue text-white text-[11px] font-semibold hover:bg-brand-blue-hover disabled:opacity-60"
                      >
                        {snmpState === 'loading' ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                        Coletar SNMP
                      </button>
                      {!d.snmp_community && <WifiOff size={12} className="text-amber-400" title="Sem community SNMP" />}
                      {sshState === 'error' && <XCircle size={12} className="text-red-400" />}
                    </div>
                  </td>
                </tr>
              )
            })}
            {!loading && devices.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-[12px] text-ink-muted">
                  Nenhum dispositivo cadastrado.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
