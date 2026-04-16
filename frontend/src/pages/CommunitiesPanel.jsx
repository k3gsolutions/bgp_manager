import { useCallback, useState } from 'react'
import { Tags } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import { useLog } from '../context/LogContext.jsx'
import CommunityLibraryTable from '../components/CommunityLibraryTable.jsx'
import CommunitySetEditor from '../components/CommunitySetEditor.jsx'

/**
 * Painel por dispositivo: biblioteca e community sets (VRP), ao mesmo nível que Interfaces / BGP.
 */
export default function CommunitiesPanel({ device }) {
  const { hasPermission } = useAuth()
  const { addLog } = useLog()

  const canView = hasPermission('communities.view')
  const canEdit = hasPermission('communities.edit')
  const canPreview = hasPermission('communities.preview')
  const canApply = hasPermission('communities.apply')
  const canResync = canEdit

  const onLog = useCallback(
    (level, message) => {
      addLog(level, 'COMMUNITIES', message)
    },
    [addLog],
  )

  const [tab, setTab] = useState('library')

  if (!canView) {
    return (
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-ink-muted">Sem permissão para ver communities neste dispositivo.</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
          <Tags size={15} className="text-ink-secondary" />
        </div>
        <div>
          <h1 className="text-[18px] font-bold text-ink-primary">BGP Communities</h1>
          <p className="text-[11px] text-ink-muted mt-0.5">
            Dispositivo: <span className="text-ink-secondary">{device.name || device.ip_address}</span>
            {' · '}
            <span className="font-mono text-[10px]">{device.ip_address}</span>
          </p>
        </div>
      </div>

      <div className="flex gap-1 p-1 rounded-lg bg-[#161922] border border-[#252840] w-fit">
        {[
          { id: 'library', label: 'Biblioteca' },
          { id: 'sets', label: 'Community Sets' },
        ].map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={[
              'px-4 py-1.5 rounded-md text-[12px] font-medium transition-colors',
              tab === t.id ? 'bg-brand-blue text-white' : 'text-ink-muted hover:text-ink-secondary',
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'library' && (
        <CommunityLibraryTable
          key={device.id}
          deviceId={device.id}
          canResync={canResync}
          onLog={onLog}
        />
      )}
      {tab === 'sets' && (
        <CommunitySetEditor
          key={device.id}
          deviceId={device.id}
          canEdit={canEdit}
          canPreview={canPreview}
          canApply={canApply}
          onLog={onLog}
        />
      )}
    </div>
  )
}
