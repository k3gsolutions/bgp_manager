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

/** Aceita envelope da API (`data`) ou só o mapa de tabelas (ex.: JSON editado). */
function extractBackupTables(parsed) {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  if (parsed.data && typeof parsed.data === 'object' && !Array.isArray(parsed.data)) {
    return parsed.data
  }
  const metaKeys = new Set(['exported_at', 'table_counts', 'imported_at', 'detail', 'message'])
  const keys = Object.keys(parsed).filter(k => !metaKeys.has(k))
  if (!keys.length) return null
  const looksLikeTables = keys.some(k => Array.isArray(parsed[k]))
  if (!looksLikeTables) return null
  return Object.fromEntries(keys.map(k => [k, parsed[k]]))
}

export default function ManagementPage() {
  const [busyExport, setBusyExport] = useState(false)
  const [busyImport, setBusyImport] = useState(false)
  const [busyCheckUpdate, setBusyCheckUpdate] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busyApplyUpdate, setBusyApplyUpdate] = useState(false)
  const [busyRollbackUpdate, setBusyRollbackUpdate] = useState(false)

  const [currentVersion, setCurrentVersion] = useState('—')
  const [checkInfo, setCheckInfo] = useState(null)
  const [updateStatus, setUpdateStatus] = useState(null)
  const [history, setHistory] = useState([])

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

  async function loadSystemVersion() {
    try {
      const data = await managementApi.getSystemVersion()
      setCurrentVersion(data.current_version || '—')
    } catch (e) {
      setErr(formatAxiosError(e))
    }
  }

  async function loadUpdateStatusAndHistory(limit = 10) {
    try {
      const s = await managementApi.getUpdateStatus()
      setUpdateStatus(s)
    } catch (e) {
      // status falhar não impede que a tela carregue
    }
    try {
      const h = await managementApi.getUpdateHistory(limit)
      setHistory(h || [])
    } catch (e) {
      // ignore
    }
  }

  async function handleCheckUpdate() {
    setErr('')
    setMsg('')
    setBusyCheckUpdate(true)
    try {
      const data = await managementApi.checkUpdate()
      setCheckInfo(data)
      if (!data.update_available) setMsg('Sistema já está atualizado.')
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyCheckUpdate(false)
    }
  }

  async function handleApplyUpdate() {
    if (!checkInfo?.update_available || !checkInfo?.update_type) return

    const updateType = checkInfo.update_type
    let confirm = false
    let confirm_strong = false
    if (updateType === 'major') {
      confirm = window.confirm('Atualização MAJOR vai alterar a aplicação. Confirmar?')
      if (!confirm) return
      confirm_strong = window.confirm('Confirmação forte: aceitar risco de instabilidade e rollback.')
      if (!confirm_strong) return
    } else if (updateType === 'minor') {
      confirm = window.confirm('Atualização MINOR exige confirmação manual. Confirmar?')
      if (!confirm) return
    } else {
      confirm = window.confirm('Atualização PATCH. Confirmar?')
      if (!confirm) return
    }

    setErr('')
    setMsg('')
    setBusyApplyUpdate(true)
    try {
      const data = await managementApi.applyUpdate({
        mode: 'manual',
        confirm,
        confirm_strong,
        target_version: checkInfo.latest_version,
      })
      setUpdateStatus(prev => ({ ...prev, running: true, status: data.status }))
      setMsg('Atualização iniciada. Acompanhe o log abaixo.')
      await loadUpdateStatusAndHistory(10)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyApplyUpdate(false)
    }
  }

  async function handleRollbackUpdate() {
    if (!history?.length) return
    const lastFailed = history.find(h => h.status === 'failed' || h.status === 'error')
    if (!lastFailed) return

    const confirm = window.confirm('Rollback vai restaurar uma versão anterior. Confirmar?')
    if (!confirm) return
    const confirm_strong = window.confirm('Confirmação forte: aceitar risco de instabilidade e interrupção curta?')
    if (!confirm_strong) return

    setErr('')
    setMsg('')
    setBusyRollbackUpdate(true)
    try {
      const data = await managementApi.rollbackUpdate({
        confirm: true,
        confirm_strong: true,
        history_id: lastFailed.id,
      })
      setUpdateStatus(prev => ({ ...prev, running: true, status: data.status }))
      setMsg('Rollback iniciado. Acompanhe o log abaixo.')
      await loadUpdateStatusAndHistory(10)
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusyRollbackUpdate(false)
    }
  }

  async function handleImportFile(file) {
    setErr('')
    setMsg('')
    if (!file) return
    const rawText = await file.text()
    const text = rawText.replace(/^\uFEFF/, '')
    let parsed
    try {
      parsed = JSON.parse(text)
    } catch {
      setErr('Arquivo inválido: JSON mal formatado.')
      return
    }
    const data = extractBackupTables(parsed)
    if (!data) {
      setErr(
        "Arquivo inválido: esperado o JSON exportado pela aplicação (objeto com 'data') ou um objeto cujas chaves são tabelas (ex.: companies, users, devices…).",
      )
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
    loadSystemVersion()
    loadUpdateStatusAndHistory(10)
  }, [])

  useEffect(() => {
    if (!updateStatus?.running) return undefined
    const t = window.setInterval(() => {
      loadUpdateStatusAndHistory(10).catch(() => {})
    }, 2500)
    return () => window.clearInterval(t)
  }, [updateStatus?.running])

  const statusLabel = useMemo(() => {
    if (updateStatus?.running) return 'Atualizando...'
    if (updateStatus?.status === 'idle') return 'Ocioso'
    return updateStatus?.status || '—'
  }, [updateStatus])

  const lastHistory = history?.[0] || null
  const lastFailedHistory = history?.find(h => h.status === 'failed' || h.status === 'error') || null

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
            Guarde o arquivo em local seguro e importe apenas em ambiente confiável. No servidor de destino, a
            mesma chave <span className="font-mono text-ink-secondary">FERNET_KEY</span> é necessária para
            desencriptar senhas SSH já gravadas; caso contrário, redefina as senhas dos equipamentos após
            importar.
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
            <p className="text-ink-primary font-mono mt-0.5">{currentVersion}</p>
          </div>
          <div className="rounded-lg border border-[#252840] bg-[#13151f] px-3 py-2">
            <p className="text-ink-muted text-[10px] uppercase tracking-wide">Última versão disponível</p>
            <p className="text-ink-primary font-mono mt-0.5">{checkInfo?.latest_version || '—'}</p>
          </div>
          <div className="rounded-lg border border-[#252840] bg-[#13151f] px-3 py-2">
            <p className="text-ink-muted text-[10px] uppercase tracking-wide">Status</p>
            <p className="text-ink-primary mt-0.5">{statusLabel}</p>
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border border-[#252840] bg-[#0f111a] p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[11px] text-ink-muted">Tipo de update</p>
              <p className="text-[11px] text-ink-primary font-mono">
                {checkInfo?.update_available ? checkInfo.update_type : '—'}
              </p>
            </div>
            {checkInfo?.latest_release_notes_summary && (
              <div className="mt-2">
                <p className="text-[11px] text-ink-muted mb-1">Changelog (resumo)</p>
                <div className="max-h-24 overflow-y-auto text-[11px] text-ink-secondary whitespace-pre-wrap">
                  {checkInfo.latest_release_notes_summary || '—'}
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleCheckUpdate}
              disabled={busyCheckUpdate || busyApplyUpdate || busyRollbackUpdate || updateStatus?.running}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-secondary text-[12px] font-semibold hover:bg-[#1e2235] disabled:opacity-50"
            >
              {busyCheckUpdate ? <Loader2 size={13} className="animate-spin" /> : <RefreshCcw size={13} />}
              Verificar agora
            </button>

            {checkInfo?.update_available && (
              <button
                type="button"
                onClick={handleApplyUpdate}
                disabled={busyApplyUpdate || busyCheckUpdate || busyRollbackUpdate || updateStatus?.running}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-brand-blue text-white text-[12px] font-semibold disabled:opacity-50"
              >
                {busyApplyUpdate || updateStatus?.running ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
                Aplicar atualização ({checkInfo.update_type})
              </button>
            )}

            {lastFailedHistory && (
              <button
                type="button"
                onClick={handleRollbackUpdate}
                disabled={busyRollbackUpdate || busyApplyUpdate || busyCheckUpdate || updateStatus?.running}
                className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-red-500/30 text-red-200 text-[12px] font-semibold hover:bg-[#1e2235] disabled:opacity-50"
              >
                {busyRollbackUpdate ? <Loader2 size={13} className="animate-spin" /> : <RefreshCcw size={13} />}
                Rollback
              </button>
            )}
          </div>

          <div className="rounded-lg border border-[#252840] bg-[#0f111a] p-3">
            <p className="text-[11px] text-ink-muted mb-1">Log do updater</p>
            <div className="max-h-52 overflow-y-auto text-[11px] font-mono text-ink-secondary whitespace-pre-wrap">
              {lastHistory?.log_text ? lastHistory.log_text : 'Sem eventos de atualização.'}
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-[#1e2235] bg-[#161922] p-4">
          <h2 className="text-[14px] font-semibold text-ink-primary mb-2">Exportar backup</h2>
          <p className="text-[12px] text-ink-muted mb-3">
            Baixa um JSON com o estado completo do banco para restauração em outro servidor (apenas
            superadmin).
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
            Restaura um backup completo exportado nesta aplicação (ou só o bloco de tabelas), substituindo os
            dados atuais. Requer superadmin.
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
