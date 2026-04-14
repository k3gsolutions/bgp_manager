import { useState, useEffect } from 'react'
import { X, Server, Loader2 } from 'lucide-react'

const VENDORS = ['Huawei', 'Cisco', 'Juniper', 'ZTE', 'MikroTik', 'Arista', 'Outro']

const EMPTY_FORM = {
  company_id: 1,
  client: '',
  name: '',
  ip_address: '',
  ssh_port: 22,
  vendor: 'Huawei',
  model: '',
  username: '',
  password: '',
  snmp_community: '',
  description: '',
}

export default function DeviceModal({ device, companies = [], onSave, onClose }) {
  const [form, setForm] = useState(EMPTY_FORM)
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const isEdit = !!device

  useEffect(() => {
    if (device) {
      setForm({
        company_id: device.company_id ?? 1,
        client: device.client || '',
        name: device.name || '',
        ip_address: device.ip_address,
        ssh_port: device.ssh_port,
        vendor: device.vendor,
        model: device.model || '',
        username: device.username,
        password: '',
        snmp_community: device.snmp_community || '',
        description: device.description || '',
      })
    } else {
      const first = companies[0]?.id
      setForm(f => ({ ...EMPTY_FORM, company_id: first ?? f.company_id ?? 1 }))
    }
  }, [device, companies])

  function set(field, value) {
    setForm(f => ({ ...f, [field]: value }))
    setErrors(e => ({ ...e, [field]: undefined }))
  }

  function validate() {
    const e = {}
    if (!companies.length) {
      e._global =
        'Cadastre pelo menos uma empresa (cliente) antes de adicionar equipamentos. O acesso dos utilizadores é definido por essa empresa.'
      return e
    }
    if (!form.company_id) e.company_id = 'Selecione a empresa (define permissões de acesso)'
    if (!form.ip_address.trim()) e.ip_address = 'Obrigatório'
    if (!form.username.trim()) e.username = 'Obrigatório'
    if (!isEdit && !form.password.trim()) e.password = 'Obrigatório'
    if (form.ssh_port < 1 || form.ssh_port > 65535) e.ssh_port = 'Porta inválida'
    return e
  }

  async function handleSubmit(e) {
    e.preventDefault()
    const errs = validate()
    if (Object.keys(errs).length) { setErrors(errs); return }
    setSaving(true)
    try {
      const payload = { ...form, company_id: Number(form.company_id), ssh_port: Number(form.ssh_port) }
      if (isEdit && !payload.password) delete payload.password
      if (!payload.client) payload.client = null
      if (!payload.name) payload.name = null
      if (!payload.model) payload.model = null
      if (!payload.snmp_community) payload.snmp_community = null
      if (!payload.description) payload.description = null
      await onSave(payload)
    } catch (err) {
      const detail = err?.response?.data?.detail
      if (typeof detail === 'string') setErrors({ _global: detail })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-lg bg-bg-surface border border-bg-border rounded-2xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bg-border">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-brand-blue-dim border border-brand-blue/20 flex items-center justify-center">
              <Server size={13} className="text-brand-blue" />
            </div>
            <h2 className="text-ink-primary font-semibold text-[15px]">
              {isEdit ? 'Editar Equipamento' : 'Novo Equipamento'}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-ink-muted hover:text-ink-primary hover:bg-bg-elevated transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="flex flex-col overflow-y-auto">
          <div className="px-6 py-5 flex flex-col gap-4">
            {errors._global && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg px-4 py-2.5 text-sm">
                {errors._global}
              </div>
            )}

            {companies.length > 0 ? (
              <Field label="Cliente / empresa (acesso) *" error={errors.company_id}>
                <select
                  value={String(form.company_id)}
                  onChange={e => set('company_id', Number(e.target.value))}
                  className={inputCls(!!errors.company_id)}
                >
                  {companies.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <p className="text-[12px] text-amber-400/90 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
                Não há empresas cadastradas. Cada equipamento deve estar atrelado a uma empresa para controlar o acesso
                dos utilizadores.
              </p>
            )}

            <div className="grid grid-cols-2 gap-4">
              <Field label="Rótulo local (opcional)">
                <input
                  value={form.client}
                  onChange={e => set('client', e.target.value)}
                  placeholder="ex: referência interna"
                  className={inputCls()}
                />
              </Field>
              <Field label="Nome (hostname)">
                <input
                  value={form.name}
                  onChange={e => set('name', e.target.value)}
                  placeholder="ex: PE-SP-01"
                  className={inputCls()}
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Endereço IP *" error={errors.ip_address}>
                <input
                  value={form.ip_address}
                  onChange={e => set('ip_address', e.target.value)}
                  placeholder="10.0.0.1"
                  disabled={isEdit}
                  className={inputCls(!!errors.ip_address, isEdit)}
                />
              </Field>
              <Field label="Porta SSH *" error={errors.ssh_port}>
                <input
                  type="number"
                  value={form.ssh_port}
                  onChange={e => set('ssh_port', e.target.value)}
                  min={1} max={65535}
                  className={inputCls(!!errors.ssh_port)}
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Fabricante">
                <select
                  value={form.vendor}
                  onChange={e => set('vendor', e.target.value)}
                  className={inputCls()}
                >
                  {VENDORS.map(v => <option key={v}>{v}</option>)}
                </select>
              </Field>
              <Field label="Modelo">
                <input
                  value={form.model}
                  onChange={e => set('model', e.target.value)}
                  placeholder="NE8000"
                  className={inputCls()}
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <Field label="Usuário SSH *" error={errors.username}>
                <input
                  value={form.username}
                  onChange={e => set('username', e.target.value)}
                  placeholder="admin"
                  autoComplete="username"
                  className={inputCls(!!errors.username)}
                />
              </Field>
              <Field label={isEdit ? 'Nova senha (deixe em branco p/ manter)' : 'Senha SSH *'} error={errors.password}>
                <input
                  type="password"
                  value={form.password}
                  onChange={e => set('password', e.target.value)}
                  placeholder={isEdit ? '••••••••' : 'senha'}
                  autoComplete="new-password"
                  className={inputCls(!!errors.password)}
                />
              </Field>
            </div>

            <Field label="Community SNMP">
              <input
                value={form.snmp_community}
                onChange={e => set('snmp_community', e.target.value)}
                placeholder="public"
                className={inputCls()}
              />
            </Field>

            <Field label="Descrição">
              <textarea
                value={form.description}
                onChange={e => set('description', e.target.value)}
                placeholder="Localização, função, observações..."
                rows={2}
                className={inputCls() + ' resize-none'}
              />
            </Field>
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-2 px-6 py-4 border-t border-bg-border">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg border border-bg-border text-ink-secondary text-sm hover:bg-bg-elevated transition-colors"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-brand-blue text-white text-sm font-medium hover:bg-brand-blue-hover disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {saving && <Loader2 size={13} className="animate-spin" />}
              {saving ? 'Salvando...' : isEdit ? 'Salvar alterações' : 'Adicionar equipamento'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Field({ label, error, children }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">{label}</label>
      {children}
      {error && <span className="text-[11px] text-red-400">{error}</span>}
    </div>
  )
}

function inputCls(hasError = false, disabled = false) {
  return [
    'w-full bg-bg-elevated border rounded-lg px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted outline-none transition-colors',
    hasError ? 'border-red-500/50 focus:border-red-500' : 'border-bg-border focus:border-brand-blue',
    disabled ? 'opacity-50 cursor-not-allowed' : '',
  ].join(' ')
}
