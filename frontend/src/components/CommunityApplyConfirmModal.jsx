import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { communitiesApi } from '../api/communities.js'
import { formatAxiosError } from '../api/client.js'

export default function CommunityApplyConfirmModal({
  open,
  onClose,
  deviceId,
  setId,
  preview,
  canApply,
  onApplied,
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ack, setAck] = useState(false)
  const [ackMissing, setAckMissing] = useState(false)

  const missingCount = preview?.members_missing_library ?? 0
  const needsMissingAck = missingCount > 0
  const commandLines = useMemo(() => {
    const body = String(preview?.candidate_config_text || '')
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean)
    return ['system-view', ...body, 'quit', 'commit', 'quit']
  }, [preview?.candidate_config_text])

  useEffect(() => {
    if (open && preview) {
      setAck(false)
      setAckMissing(false)
      setErr('')
      setBusy(false)
    }
  }, [open, preview?.candidate_sha256])

  if (!open || !preview) return null

  async function apply() {
    if (!canApply || !ack || (needsMissingAck && !ackMissing)) return
    setBusy(true)
    setErr('')
    try {
      await communitiesApi.applySet(deviceId, setId, {
        confirm: true,
        expected_candidate_sha256: preview.candidate_sha256,
        acknowledge_missing_library_refs: needsMissingAck ? ackMissing : false,
      })
      onApplied?.()
      onClose()
    } catch (e) {
      setErr(formatAxiosError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60">
      <div className="w-full max-w-2xl rounded-xl border border-[#252840] bg-[#13151f] shadow-xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#252840]">
          <div className="flex items-center gap-2 text-ink-primary text-[14px] font-semibold">
            <AlertTriangle size={16} className="text-amber-400" />
            Confirmar aplicação no roteador
          </div>
          <button type="button" onClick={onClose} className="p-1 rounded-lg text-ink-muted hover:text-ink-primary">
            <X size={18} />
          </button>
        </div>

        <div className="px-4 py-3 overflow-y-auto flex-1 flex flex-col gap-3">
          {(preview.warnings || []).length > 0 && (
            <div className="text-[12px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2">
              {(preview.warnings || []).map((w, i) => (
                <p key={i}>{w}</p>
              ))}
            </div>
          )}

          {needsMissingAck && (
            <div className="text-[12px] text-red-200 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2 space-y-1">
              <p className="font-semibold">
                {missingCount} valor(es) sem ``community-filter`` correspondente na biblioteca:
              </p>
              <p className="font-mono text-[11px] break-all">
                {(preview.missing_community_values || []).join(', ') || '—'}
              </p>
              <p className="text-[11px] text-ink-muted">
                A aplicação está bloqueada por defeito. Só prossiga se aceitar o risco operacional.
              </p>
            </div>
          )}

          <p className="text-[11px] text-ink-muted">Comandos que serão enviados ao dispositivo via SSH:</p>
          <pre className="text-[11px] font-mono bg-[#0f111a] border border-[#252840] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-ink-secondary">
            {commandLines.join('\n')}
          </pre>
          <p className="text-[11px] text-ink-muted">Bloco da community-list gerado para esta operação:</p>
          <pre className="text-[11px] font-mono bg-[#0f111a] border border-[#252840] rounded-lg p-3 overflow-x-auto whitespace-pre-wrap text-ink-secondary">
            {preview.candidate_config_text}
          </pre>
          <p className="text-[10px] text-ink-muted font-mono break-all">SHA-256: {preview.candidate_sha256}</p>

          {err && (
            <div className="text-[12px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
              {err}
            </div>
          )}
        </div>

        <div className="px-4 py-3 border-t border-[#252840] flex flex-col gap-3">
          {canApply ? (
            <div className="flex flex-col gap-2">
              <label className="flex items-start gap-2 text-[12px] text-ink-secondary cursor-pointer">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
                Confirmo que esta ação irá alterar configuração no dispositivo e pode causar instabilidade.
              </label>
              {needsMissingAck ? (
                <label className="flex items-start gap-2 text-[12px] text-amber-200/95 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={ackMissing}
                    onChange={(e) => setAckMissing(e.target.checked)}
                    className="mt-0.5"
                  />
                  Aceito aplicar mesmo com communities ausentes na biblioteca (risco explícito).
                </label>
              ) : null}
            </div>
          ) : (
            <p className="text-[12px] text-ink-muted">A sua função não inclui permissão para aplicar no roteador.</p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-2 rounded-lg border border-[#252840] text-[12px] text-ink-secondary hover:bg-[#1a1d2e]"
            >
              Cancelar
            </button>
            <button
              type="button"
              disabled={!canApply || !ack || busy || (needsMissingAck && !ackMissing)}
              onClick={apply}
              className="px-3 py-2 rounded-lg bg-brand-blue text-white text-[12px] hover:bg-brand-blue-hover disabled:opacity-40"
            >
              {busy ? 'A aplicar…' : 'Confirmar e aplicar'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
