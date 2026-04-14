import {
  LayoutDashboard,
  Server,
  Terminal,
  Zap,
  PanelLeftClose,
  PanelLeftOpen,
  Building2,
  Users,
  Database,
} from 'lucide-react'
import { useState } from 'react'
import { useLog } from '../context/LogContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'

const BASE_NAV = [
  { key: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, perm: null },
  { key: 'devices', label: 'Dispositivos', icon: Server, perm: 'devices.view' },
  { key: 'logs', label: 'Log', icon: Terminal, perm: 'logs.view' },
]

const EXTRA_NAV = [
  { key: 'companies', label: 'Empresas', icon: Building2, perm: 'companies.view' },
  { key: 'users', label: 'Usuários', icon: Users, perm: 'users.view' },
  { key: 'management', label: 'Gerenciamento', icon: Database, perm: 'management.backup' },
]

export default function Sidebar({ activePage, onNavigate, deviceCount }) {
  const [collapsed, setCollapsed] = useState(false)
  const { unread } = useLog()
  const { hasPermission } = useAuth()

  const NAV = [
    ...BASE_NAV.filter(item => !item.perm || hasPermission(item.perm)),
    ...EXTRA_NAV.filter(item => hasPermission(item.perm)),
  ]

  function badge(key) {
    if (key === 'devices') return deviceCount ?? null
    if (key === 'logs') return unread > 0 ? unread : null
    return null
  }

  if (collapsed) {
    return (
      <aside className="flex flex-col w-12 shrink-0 bg-[#161922] border-r border-[#1e2235] min-h-screen items-center py-3 gap-3">
        <div className="w-7 h-7 rounded-lg bg-brand-blue flex items-center justify-center">
          <Zap size={13} className="text-white" />
        </div>
        <div className="flex-1 mt-2 flex flex-col gap-1 w-full px-1.5">
          {NAV.map(({ key, icon: Icon, label, disabled }) => {
            const b = badge(key)
            return (
              <button
                key={key}
                title={label}
                onClick={() => !disabled && onNavigate(key)}
                className={[
                  'relative flex items-center justify-center p-2 rounded-lg w-full transition-colors',
                  disabled ? 'opacity-40 cursor-default' : '',
                  activePage === key
                    ? 'bg-[#1e2a45] text-brand-blue'
                    : 'text-[#8892a4] hover:bg-[#1c1f30] hover:text-[#c8d0de]',
                ].join(' ')}
              >
                <Icon size={14} />
                {b !== null && (
                  <span className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-red-500 text-white text-[8px] font-bold flex items-center justify-center">
                    {b > 9 ? '9+' : b}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        <button
          onClick={() => setCollapsed(false)}
          className="p-2 rounded-lg text-ink-muted hover:text-ink-primary hover:bg-bg-elevated transition-colors"
        >
          <PanelLeftOpen size={14} />
        </button>
      </aside>
    )
  }

  return (
    <aside className="flex flex-col w-48 shrink-0 bg-[#161922] border-r border-[#1e2235] min-h-screen">
      <div className="flex items-center gap-2 px-4 py-3.5 border-b border-[#1e2235]">
        <div className="w-7 h-7 rounded-lg bg-brand-blue flex items-center justify-center shrink-0">
          <Zap size={13} className="text-white" />
        </div>
        <div>
          <p className="text-ink-primary text-[13px] font-bold leading-none tracking-tight">BGP Manager</p>
          <p className="text-ink-muted text-[10px] mt-0.5">Network Ops</p>
        </div>
      </div>

      <nav className="flex-1 py-3 px-2 flex flex-col gap-0.5">
        {NAV.map(({ key, label, icon: Icon, disabled }) => {
          const active = activePage === key
          const b = badge(key)
          return (
            <button
              key={key}
              onClick={() => !disabled && onNavigate(key)}
              className={[
                'relative flex items-center gap-2 w-full px-2.5 py-2 rounded-lg text-[13px] transition-all text-left',
                disabled ? 'opacity-40 cursor-default' : 'cursor-pointer',
                active
                  ? 'bg-[#1e2a45] text-white'
                  : 'text-[#8892a4] hover:bg-[#1c1f30] hover:text-[#c8d0de]',
              ].join(' ')}
            >
              {active && (
                <span className="absolute left-0 top-1.5 bottom-1.5 w-0.5 bg-brand-blue rounded-full" />
              )}
              <Icon size={14} className={active ? 'text-brand-blue' : ''} />
              <span className="flex-1">{label}</span>
              {b !== null && (
                <span className={[
                  'text-[10px] px-1.5 py-0.5 rounded-full font-semibold min-w-[18px] text-center',
                  key === 'logs' && !active
                    ? 'bg-red-500 text-white'
                    : active
                    ? 'bg-brand-blue text-white'
                    : 'bg-[#252840] text-[#8892a4]',
                ].join(' ')}>
                  {b}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      <div className="px-2 py-2 border-t border-[#1e2235]">
        <button
          onClick={() => setCollapsed(true)}
          className="flex items-center gap-2 w-full px-2.5 py-1.5 rounded-lg text-[12px] text-[#8892a4] hover:text-[#c8d0de] hover:bg-[#1c1f30] transition-colors"
        >
          <PanelLeftClose size={13} />
          Recolher menu
        </button>
      </div>
    </aside>
  )
}
