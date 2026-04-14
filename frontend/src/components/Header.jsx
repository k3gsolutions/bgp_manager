import { ChevronRight, LogOut } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'

/**
 * @param {{ label: string, onClick?: () => void }[]} [breadcrumbItems]
 * @param {string[]} [breadcrumbs] — legado: só texto
 */
export default function Header({ breadcrumbs = [], breadcrumbItems }) {
  const { me, logout } = useAuth()
  const name = me?.username || '—'
  const role = me?.role || ''
  const initials = (name || '?').slice(0, 2).toUpperCase()

  const items =
    breadcrumbItems ||
    breadcrumbs.map(label => ({ label }))

  return (
    <header className="h-11 border-b border-[#1e2235] bg-[#161922] flex items-center justify-between px-5 shrink-0">
      <nav className="flex items-center gap-1 text-[12px]">
        {items.map((crumb, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={11} className="text-[#4a5568]" />}
            {crumb.onClick ? (
              <button
                type="button"
                onClick={crumb.onClick}
                className={
                  i === items.length - 1
                    ? 'text-ink-primary font-medium hover:text-brand-blue hover:underline text-left'
                    : 'text-[#64748b] hover:text-ink-secondary'
                }
              >
                {crumb.label}
              </button>
            ) : (
              <span className={i === items.length - 1 ? 'text-ink-primary font-medium' : 'text-[#64748b]'}>
                {crumb.label}
              </span>
            )}
          </span>
        ))}
      </nav>

      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 rounded-full bg-brand-blue flex items-center justify-center text-white text-[11px] font-bold">
          {initials}
        </div>
        <div className="leading-none">
          <p className="text-ink-primary text-[12px] font-semibold">{name}</p>
          <p className="text-[10px] text-ink-muted mt-0.5">{role}</p>
        </div>
        <button
          type="button"
          onClick={() => logout()}
          title="Sair"
          className="ml-1 p-1.5 rounded-md text-ink-muted hover:text-ink-primary hover:bg-[#1e2235] transition-colors"
        >
          <LogOut size={13} />
        </button>
      </div>
    </header>
  )
}
