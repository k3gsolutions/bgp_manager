import { useEffect, useMemo, useState } from 'react'
import { Database, Download, Upload, Loader2, AlertTriangle, CheckCircle2, RefreshCcw, Rocket } from 'lucide-react'
import { managementApi } from '../api/management.js'
import { formatAxiosError } from '../api/client.js'

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function ManagementPage() {
  const [busyExport, setBusyExport] = useState(false)
  const [busyImport, setBusyImport] = useState(false)
  const [busyCheckUpdate, setBusyCheckUpdate] = useState(false)
  const [busyRunUpdate, setBusyRunUpdate] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [updateInfo, setUpdateInfo] = useState(null)

  async function handleExport() {
    setErr('')
    setMsg('')
    setBusyExport(true)
    try {
      const data = await managementApi.exportBackup()
      const ts = new Date().toISOString().replace(/[:.]/g, '-')
      downloadJson(`bgp-manager-backup-${ts}.json`, data)
      setMsg(`Backup exportado (${Object.values(data.table_counts || {}).reduce((a, b) => a + b, 0)} registros).`)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyExport(false)
    }
  }

  async function loadUpdateStatus() {
    try {
      const data = await managementApi.getUpdateStatus()
      setUpdateInfo(data)
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  async function handleCheckUpdate() {
    setErr('')
    setMsg('')
    setBusyCheckUpdate(true)
    try {
      const data = await managementApi.checkUpdate()
      setUpdateInfo(data)
      if (data.status === 'up_to_date') setMsg('Sistema já está atualizado.')
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyCheckUpdate(false)
    }
  }

  async function handleRunUpdate() {
    if (!window.confirm('Executar atualização do sistema agora?')) return
    setErr('')
    setMsg('')
    setBusyRunUpdate(true)
    try {
      const data = await managementApi.runUpdate()
      setUpdateInfo(data)
      setMsg('Atualização iniciada. Acompanhe o progresso no log abaixo.')
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyRunUpdate(false)
    }
  }

  async function handleImportFile(file) {
    setErr('')
    setMsg('')
    if (!file) return
    const text = await file.text()
    let parsed
    try {
      parsed = JSON.parse(text)
    } catch {
      setErr('Arquivo inválido: JSON mal formatado.')
      return
    }
    const data = parsed?.data
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      setErr("Arquivo inválido: campo 'data' não encontrado.")
      return
    }
    if (!window.confirm('Importar backup completo? Os registros atuais serão substituídos.')) return
    setBusyImport(true)
    try {
      const res = await managementApi.importBackup(data)
      const total = Object.values(res.table_counts || {}).reduce((a, b) => a + b, 0)
      setMsg(`Backup importado com sucesso (${total} registros).`)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyImport(false)
    }
  }

  useEffect(() => {
    loadUpdateStatus()
  }, [])

  useEffect(() => {
    if (!updateInfo?.running) return undefined
    const t = window.setInterval(() => {
      managementApi.getUpdateStatus().then(setUpdateInfo).catch(() => {})
    }, 2500)
    return () => window.clearInterval(t)
  }, [updateInfo?.running])

  const statusLabel = useMemo(() => {
    const s = updateInfo?.status
    if (!s) return '—'
    if (s === 'up_to_date') return 'Atualizado'
    if (s === 'update_available') return 'Atualização disponível'
    if (s === 'running') return 'Atualizando...'
    if (s === 'checking') return 'Verificando...'
    if (s === 'success') return 'Atualização concluída'
    if (s === 'error') return 'Erro ao atualizar'
    return s
  }, [updateInfo?.status])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Database size={18} className="text-ink-secondary" />
        <h1 className="text-[18px] font-bold text-ink-primary">Gerenciamento</h1>
      </div>

      <div className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-[12px] text-amber-100">
        <div className="flex items-start gap-2">
          <AlertTriangle size={15} className="mt-0.5 text-amber-300" />
          <p>
            Backup inclui dados sensíveis (utilizadores, hashes e credenciais cifradas de dispositivos).
            Guarde o arquivo em local seguro e importe apenas em ambiente confiável.
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-[#1e2235] bg-[#161922] p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Rocket size={15} className="text-brand-blue" />
          <h2 className="text-[14px] font-semibold text-ink-primary">Atualização do Sistema</h2>
        </div>

        <div className="grid gap-2 md:grid-cols-3 text-[12px]">
          <div className="rounded-lg border border-[#252840] bg-[#13151f] px-3 py-2">
            <p className="text-ink-muted text-[10px] uppercase tracking-wide">Versão atual</p>
            <p className="text-ink-primary font-mono mt-0.5">{updateInfo?.current_version || '—'}</p>
          </div>
          <div className="rounded-lg border border-[#252840] bg-[#13151f] px-3 py-2">
            <p className="text-ink-muted text-[10px] uppercase tracking-wide">Última versão disponível</p>
            <p className="text-ink-primary font-mono mt-0.5">{updateInfo?.latest_version || '—'}</p>
          </div>
          <div className="rounded-lg border border-[#252840] bg-[#13151f] px-3 py-2">
            <p className="text-ink-muted text-[10px] uppercase tracking-wide">Status</p>
            <p className="text-ink-primary mt-0.5">{statusLabel}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleCheckUpdate}
            disabled={busyCheckUpdate || busyRunUpdate || updateInfo?.running}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary text-[12px] font-semibold hover:bg-[#1e2235] disabled:opacity-50"
          >
            {busyCheckUpdate ? <Loader2 size={13} className="animate-spin" /> : <RefreshCcw size={13} />}
            Verificar atualização
          </button>

          {(updateInfo?.update_available || updateInfo?.status === 'update_available') && (
            <button
              type="button"
              onClick={handleRunUpdate}
              disabled={busyRunUpdate || busyCheckUpdate || updateInfo?.running}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold disabled:opacity-50"
            >
              {busyRunUpdate || updateInfo?.running ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
              Atualizar versão
            </button>
          )}
        </div>

        <div className="rounded-lg border border-[#252840] bg-[#0f111a] p-3">
          <p className="text-[11px] text-ink-muted mb-1">Log da atualização</p>
          <div className="max-h-52 overflow-y-auto text-[11px] font-mono text-ink-secondary whitespace-pre-wrap">
            {updateInfo?.logs?.length
              ? updateInfo.logs.slice().reverse().join('\n')
              : 'Sem eventos de atualização.'}
          </div>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-[#1e2235] bg-[#161922] p-4">
          <h2 className="text-[14px] font-semibold text-ink-primary mb-2">Exportar backup</h2>
          <p className="text-[12px] text-ink-muted mb-3">
            Baixa um JSON com o estado completo do banco para restauração em outro servidor.
          </p>
          <button
            type="button"
            onClick={handleExport}
            disabled={busyExport || busyImport}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold disabled:opacity-50"
          >
            {busyExport ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
            Exportar backup
          </button>
        </div>

        <div className="rounded-xl border border-[#1e2235] bg-[#161922] p-4">
          <h2 className="text-[14px] font-semibold text-ink-primary mb-2">Importar backup</h2>
          <p className="text-[12px] text-ink-muted mb-3">
            Restaura um backup completo previamente exportado, substituindo os dados atuais.
          </p>
          <label className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary text-[12px] hover:bg-[#1e2235] cursor-pointer">
            {busyImport ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            Selecionar backup (.json)
            <input
              type="file"
              accept="application/json,.json"
              disabled={busyExport || busyImport}
              className="hidden"
              onChange={e => handleImportFile(e.target.files?.[0] || null)}
            />
          </label>
        </div>
      </div>

      {msg && (
        <div className="rounded-lg border border-green-500/30 bg-green-500/10 px-3 py-2 text-[12px] text-green-300 flex items-center gap-2">
          <CheckCircle2 size={14} />
          {msg}
        </div>
      )}
      {err && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
          {err}
        </div>
      )}
    </div>
  )
}
