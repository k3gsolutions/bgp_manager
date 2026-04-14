import { Filter } from 'lucide-react'

export default function FiltrosPanel({ device }) {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
          <Filter size={15} className="text-ink-secondary" />
        </div>
        <div>
          <h1 className="text-[18px] font-bold text-ink-primary">Filtros de Rota</h1>
          <p className="text-[11px] text-ink-muted mt-0.5">
            {device.name || device.ip_address}
          </p>
        </div>
      </div>

      <div className="flex flex-col items-center justify-center py-20 gap-3 text-ink-muted">
        <Filter size={32} className="opacity-20" />
        <p className="text-[13px] text-ink-secondary">Módulo em desenvolvimento</p>
        <p className="text-[11px]">Análise de route-policy e prefix-list via SSH/SNMP</p>
      </div>
    </div>
  )
}
