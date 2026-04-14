import { useRef, useState } from 'react'
import { X, Upload, FileDown, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { devicesApi } from '../api/devices.js'
import { parseDeviceImportFile } from '../utils/deviceImportParser.js'

export default function DeviceImportModal({ companies, onClose, onImported }) {
  const inputRef = useRef(null)
  const [fileName, setFileName] = useState('')
  const [devices, setDevices] = useState([])
  const [parseErrors, setParseErrors] = useState([])
  const [rowErrors, setRowErrors] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)

  function resetState() {
    setFileName('')
    setDevices([])
    setParseErrors([])
    setRowErrors([])
    setResult(null)
  }

  function handlePick() {
    inputRef.current?.click()
  }

  function handleFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    resetState()
    setFileName(file.name)
    const reader = new FileReader()
    reader.onload = () => {
      const text = String(reader.result || '')
      const r = parseDeviceImportFile(file, text, companies)
      setDevices(r.devices)
      setParseErrors(r.parseErrors)
      setRowErrors(r.rowErrors)
    }
    reader.onerror = () => {
      setParseErrors(['Não foi possível ler o ficheiro.'])
    }
    reader.readAsText(file, 'UTF-8')
  }

  async function handleSubmit() {
    if (!devices.length || parseErrors.length || rowErrors.length) return
    setSubmitting(true)
    setResult(null)
    try {
      const res = await devicesApi.batchCreate(devices)
      setResult(res)
      if (res.created?.length) onImported?.(res)
    } catch (err) {
      const d = err?.response?.data?.detail
      setResult({
        error: typeof d === 'string' ? d : err?.message || 'Falha na importação',
      })
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = devices.length > 0 && !parseErrors.length && !rowErrors.length && !submitting

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-2xl bg-bg-surface border border-bg-border rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-bg-border">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-brand-blue-dim border border-brand-blue/20 flex items-center justify-center">
              <Upload size={13} className="text-brand-blue" />
            </div>
            <h2 className="text-ink-primary font-semibold text-[15px]">Importar equipamentos (CSV / XML)</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-ink-muted hover:text-ink-primary hover:bg-bg-elevated transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        <div className="px-6 py-4 flex flex-col gap-4 overflow-y-auto text-[12px] text-ink-secondary">
          <p className="text-ink-muted leading-relaxed">
            Campos obrigatórios: <span className="text-ink-secondary font-mono text-[11px]">company_id</span>
            {' '}ou <span className="text-ink-secondary font-mono text-[11px]">company_name</span>
            {' '}(nome exato da empresa no sistema),{' '}
            <span className="text-ink-secondary font-mono text-[11px]">ip_address</span>,{' '}
            <span className="text-ink-secondary font-mono text-[11px]">username</span>,{' '}
            <span className="text-ink-secondary font-mono text-[11px]">password</span>.
            Opcionais: client, name, ssh_port (padrão 22), vendor, model, snmp_community, description.
          </p>

          <div className="flex flex-wrap gap-2">
            <a
              href="/templates/dispositivos-exemplo.csv"
              download
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-bg-border text-ink-secondary hover:bg-bg-elevated transition-colors"
            >
              <FileDown size={12} />
              Modelo CSV
            </a>
            <a
              href="/templates/dispositivos-exemplo.xml"
              download
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-bg-border text-ink-secondary hover:bg-bg-elevated transition-colors"
            >
              <FileDown size={12} />
              Modelo XML
            </a>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,.xml,text/csv,application/xml,text/xml"
              className="hidden"
              onChange={handleFile}
            />
            <button
              type="button"
              onClick={handlePick}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold hover:bg-brand-blue-hover transition-colors"
            >
              <Upload size={12} />
              Escolher ficheiro
            </button>
          </div>

          {fileName && (
            <p className="text-[11px] text-ink-muted">
              Ficheiro: <span className="font-mono text-ink-secondary">{fileName}</span>
              {' · '}
              {devices.length} linha{devices.length !== 1 ? 's' : ''}
            </p>
          )}

          {parseErrors.length > 0 && (
            <div className="flex gap-2 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-red-400">
              <AlertCircle size={14} className="shrink-0 mt-0.5" />
              <ul className="list-disc pl-4 space-y-0.5">
                {parseErrors.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          )}

          {rowErrors.length > 0 && (
            <div className="flex gap-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-amber-200 text-[11px] max-h-32 overflow-y-auto">
              <AlertCircle size={14} className="shrink-0 mt-0.5 text-amber-400" />
              <ul className="list-disc pl-4 space-y-0.5">
                {rowErrors.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </div>
          )}

          {devices.length > 0 && !parseErrors.length && !rowErrors.length && (
            <div className="rounded-lg border border-bg-border bg-bg-elevated overflow-hidden max-h-40 overflow-y-auto">
              <table className="w-full text-[11px] font-mono">
                <thead>
                  <tr className="text-ink-muted border-b border-bg-border text-left">
                    <th className="px-2 py-1.5">#</th>
                    <th className="px-2 py-1.5">IP</th>
                    <th className="px-2 py-1.5">empresa</th>
                    <th className="px-2 py-1.5">user</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.slice(0, 50).map((d, i) => (
                    <tr key={i} className="border-b border-bg-border/60 last:border-0">
                      <td className="px-2 py-1 text-ink-muted">{i + 1}</td>
                      <td className="px-2 py-1 text-ink-primary">{d.ip_address}</td>
                      <td className="px-2 py-1">{d.company_id}</td>
                      <td className="px-2 py-1 truncate max-w-[120px]">{d.username}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {devices.length > 50 && (
                <p className="px-2 py-1 text-[10px] text-ink-muted">… e mais {devices.length - 50} linhas</p>
              )}
            </div>
          )}

          {result?.error && (
            <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-red-400 text-[11px]">
              {result.error}
            </div>
          )}

          {result && !result.error && Array.isArray(result.created) && (
            <div className="space-y-2 rounded-lg border border-bg-border bg-bg-elevated px-3 py-2">
              <div className="flex items-center gap-2 text-green-400 text-[12px] font-medium">
                <CheckCircle2 size={14} />
                Criados: {result.created.length} · Falhas: {result.failed?.length ?? 0}
              </div>
              {result.failed?.length > 0 && (
                <ul className="text-[10px] text-amber-200/90 list-disc pl-4 max-h-24 overflow-y-auto">
                  {result.failed.map((f, i) => (
                    <li key={i}>
                      Linha {f.index + 1}
                      {f.ip_address ? ` (${f.ip_address})` : ''}: {f.detail}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-6 py-4 border-t border-bg-border">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg border border-bg-border text-ink-secondary text-sm hover:bg-bg-elevated transition-colors"
          >
            Fechar
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={handleSubmit}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-blue text-white text-sm font-medium hover:bg-brand-blue-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting && <Loader2 size={13} className="animate-spin" />}
            {submitting ? 'A importar…' : 'Importar'}
          </button>
        </div>
      </div>
    </div>
  )
}
