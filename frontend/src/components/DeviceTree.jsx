import { useState } from 'react'
import {
  Server, ChevronRight, ChevronDown,
  Network, Filter, GitBranch, Plus, RefreshCw
} from 'lucide-react'

const VIEWS = [
  { key: 'interfaces', label: 'Interfaces', icon: GitBranch },
  { key: 'bgp',        label: 'BGP',        icon: Network },
  { key: 'filtros',    label: 'Filtros',    icon: Filter },
]

// 'lookup' é a view padrão ao clicar no nome do dispositivo (não aparece como sub-item)

function groupByClient(devices) {
  const map = {}
  for (const d of devices) {
    const key = d.client || 'Sem cliente'
    if (!map[key]) map[key] = []
    map[key].push(d)
  }
  return Object.entries(map).sort(([a], [b]) => a.localeCompare(b))
}

export default function DeviceTree({ devices, selected, onSelect, onNewDevice }) {
  const [expandedClients, setExpandedClients] = useState({})
  const [expandedDevices, setExpandedDevices] = useState({})

  const groups = groupByClient(devices)

  function toggleClient(client) {
    setExpandedClients(s => ({ ...s, [client]: !s[client] }))
  }

  function toggleDevice(id) {
    setExpandedDevices(s => ({ ...s, [id]: !s[id] }))
  }

  function selectView(device, view) {
    onSelect({ device, view })
    setExpandedDevices(s => ({ ...s, [device.id]: true }))
  }

  function clickDeviceName(device) {
    // Seleciona o dispositivo na view de busca de prefixo e expande o nó
    onSelect({ device, view: 'lookup' })
    setExpandedDevices(s => ({ ...s, [device.id]: true }))
  }

  const isDeviceExpanded = (id) => expandedDevices[id] ?? false
  const isClientExpanded = (client) => expandedClients[client] ?? true // open by default

  return (
    <aside className="flex flex-col w-56 shrink-0 bg-[#13151f] border-r border-[#1e2235] min-h-screen">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-[#1e2235]">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          Dispositivos
        </span>
        <button
          onClick={onNewDevice}
          className="p-1 rounded-md text-ink-muted hover:text-brand-blue hover:bg-brand-blue-dim transition-colors"
          title="Novo dispositivo"
        >
          <Plus size={13} />
        </button>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {groups.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 px-4 text-center gap-2">
            <Server size={24} className="text-ink-muted opacity-30" />
            <p className="text-[11px] text-ink-muted">Nenhum dispositivo</p>
            <button
              onClick={onNewDevice}
              className="text-[11px] text-brand-blue hover:underline"
            >
              Adicionar
            </button>
          </div>
        ) : (
          groups.map(([client, clientDevices]) => (
            <div key={client}>
              {/* CLIENT node */}
              <button
                onClick={() => toggleClient(client)}
                className="flex items-center gap-1.5 w-full px-3 py-1.5 text-left hover:bg-[#1a1d2e] transition-colors group"
              >
                {isClientExpanded(client)
                  ? <ChevronDown size={11} className="text-ink-muted shrink-0" />
                  : <ChevronRight size={11} className="text-ink-muted shrink-0" />}
                <span className="text-[11px] font-semibold text-ink-secondary uppercase tracking-wide truncate">
                  {client}
                </span>
                <span className="ml-auto text-[9px] bg-[#1e2235] text-ink-muted px-1.5 py-0.5 rounded-full shrink-0">
                  {clientDevices.length}
                </span>
              </button>

              {isClientExpanded(client) && clientDevices.map(device => {
                const devExpanded = isDeviceExpanded(device.id)
                const isActiveDevice = selected?.device?.id === device.id

                return (
                  <div key={device.id}>
                    {/* DEVICE node — clique no nome abre busca; chevron expande/colapsa */}
                    <div className={[
                      'flex items-center gap-1.5 w-full pl-6 pr-3 py-1.5 transition-colors',
                      isActiveDevice
                        ? 'bg-[#1e2a45] text-white'
                        : 'hover:bg-[#1a1d2e] text-ink-secondary',
                    ].join(' ')}>
                      <button
                        type="button"
                        onClick={() => toggleDevice(device.id)}
                        className="shrink-0 p-0.5 -ml-0.5 rounded hover:bg-[#252840] transition-colors"
                        aria-label={devExpanded ? 'Recolher' : 'Expandir'}
                      >
                        {devExpanded
                          ? <ChevronDown size={10} className="text-ink-muted" />
                          : <ChevronRight size={10} className="text-ink-muted" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => clickDeviceName(device)}
                        className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
                      >
                        <Server size={11} className={isActiveDevice ? 'text-brand-blue shrink-0' : 'text-ink-muted shrink-0'} />
                        <span className="text-[12px] truncate">
                          {device.name || device.ip_address}
                        </span>
                      </button>
                    </div>

                    {/* VIEWS (Interfaces, BGP, Filtros) */}
                    {devExpanded && VIEWS.map(({ key, label, icon: Icon }) => {
                      const isActive = isActiveDevice && selected?.view === key
                      return (
                        <button
                          key={key}
                          onClick={() => selectView(device, key)}
                          className={[
                            'flex items-center gap-2 w-full pl-10 pr-3 py-1.5 text-left transition-colors',
                            isActive
                              ? 'bg-brand-blue-dim text-brand-blue'
                              : 'text-ink-muted hover:bg-[#1a1d2e] hover:text-ink-secondary',
                          ].join(' ')}
                        >
                          <Icon size={11} className="shrink-0" />
                          <span className="text-[11.5px]">{label}</span>
                        </button>
                      )
                    })}
                  </div>
                )
              })}
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
