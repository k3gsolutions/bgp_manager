import { useEffect, useMemo, useState } from 'react'
import { Terminal, Trash2, CheckCircle2, AlertTriangle, XCircle, Info } from 'lucide-react'
import { useLog } from '../context/LogContext.jsx'
import { logsApi } from '../api/logs.js'

const LEVEL_CFG = {
  error:   { icon: XCircle,        color: 'text-red-400',    bg: 'bg-red-500/8',    border: 'border-red-500/20',    label: 'ERRO' },
  warn:    { icon: AlertTriangle,   color: 'text-yellow-400', bg: 'bg-yellow-500/8', border: 'border-yellow-500/20', label: 'AVISO' },
  success: { icon: CheckCircle2,    color: 'text-green-400',  bg: 'bg-green-500/8',  border: 'border-green-500/20',  label: 'OK' },
  info:    { icon: Info,            color: 'text-blue-400',   bg: 'bg-blue-500/8',   border: 'border-blue-500/20',   label: 'INFO' },
}

function fmt(date) {
  return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function normalizeLevel(level) {
  const lv = String(level || 'info').toLowerCase()
  if (lv === 'warning') return 'warn'
  if (lv === 'err') return 'error'
  return lv
}

export default function LogPanel() {
  const { entries, clearAll, clearUnread } = useLog()
  const [backendEntries, setBackendEntries] = useState([])
  const [levelFilter, setLevelFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')

  useEffect(() => {
    clearUnread()
  }, [clearUnread])

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const data = await logsApi.recent(100)
        if (!active) return
        const mapped = (Array.isArray(data) ? data : []).map((x, i) => ({
          id: `b-${x.timestamp || ''}-${i}`,
          timestamp: x.timestamp ? new Date(x.timestamp) : new Date(),
          level: normalizeLevel(x.level),
          source: x.source || 'BACKEND',
          message: x.message || '',
          detail: x.detail || null,
        }))
        setBackendEntries(mapped)
      } catch {
        // mantém o log local de fallback
      }
    }
    load()
    const t = window.setInterval(load, 7000)
    return () => {
      active = false
      window.clearInterval(t)
    }
  }, [])

  const mergedEntries = useMemo(() => {
    const all = [...backendEntries, ...entries].map(e => ({ ...e, level: normalizeLevel(e.level) }))
    all.sort((a, b) => b.timestamp - a.timestamp)
    return all.slice(0, 100)
  }, [backendEntries, entries])

  const sourceOptions = useMemo(() => {
    const set = new Set(['all'])
    for (const e of mergedEntries) {
      if (e.source) set.add(e.source)
    }
    return Array.from(set)
  }, [mergedEntries])

  const filteredEntries = useMemo(() => {
    return mergedEntries.filter(entry => {
      const matchLevel = levelFilter === 'all' || entry.level === levelFilter
      const matchSource = sourceFilter === 'all' || entry.source === sourceFilter
      return matchLevel && matchSource
    })
  }, [mergedEntries, levelFilter, sourceFilter])

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[#1e2235] border border-[#252840] flex items-center justify-center">
            <Terminal size={15} className="text-ink-secondary" />
          </div>
          <div>
            <h1 className="text-[18px] font-bold text-ink-primary">Log de Eventos</h1>
            <p className="text-[11px] text-ink-muted mt-0.5">
              {filteredEntries.length} registro{filteredEntries.length !== 1 ? 's' : ''}
              {` (de ${mergedEntries.length} nos últimos 100)`}
              {filteredEntries.length > 0 && ` · mais recente: ${fmt(filteredEntries[0].timestamp)}`}
            </p>
          </div>
        </div>
        {mergedEntries.length > 0 && (
          <button
            onClick={clearAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#252840] text-ink-muted text-[12px] hover:text-red-400 hover:border-red-500/30 hover:bg-red-500/5 transition-colors"
          >
            <Trash2 size={12} />
            Limpar log
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={levelFilter}
          onChange={e => setLevelFilter(e.target.value)}
          className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px] text-ink-primary outline-none focus:border-brand-blue transition-colors"
        >
          <option value="all">Nível: todos</option>
          <option value="error">Nível: error</option>
          <option value="warn">Nível: warn</option>
          <option value="info">Nível: info</option>
          <option value="success">Nível: success</option>
        </select>
        <select
          value={sourceFilter}
          onChange={e => setSourceFilter(e.target.value)}
          className="bg-[#161922] border border-[#252840] rounded-lg px-3 py-1.5 text-[12px] text-ink-primary outline-none focus:border-brand-blue transition-colors"
        >
          <option value="all">Origem: todas</option>
          {sourceOptions.filter(x => x !== 'all').map(source => (
            <option key={source} value={source}>
              {`Origem: ${source}`}
            </option>
          ))}
        </select>
      </div>

      {/* Log entries */}
      {filteredEntries.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3 text-ink-muted">
          <Terminal size={32} className="opacity-20" />
          <p className="text-[13px] text-ink-secondary">
            {mergedEntries.length === 0 ? 'Nenhum evento registrado' : 'Nenhum evento para os filtros selecionados'}
          </p>
          <p className="text-[11px]">Erros de SSH, SNMP e ações serão exibidos aqui</p>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          {filteredEntries.map(entry => {
            const cfg = LEVEL_CFG[entry.level] || LEVEL_CFG.info
            const Icon = cfg.icon
            return (
              <div
                key={entry.id}
                className={`flex gap-3 px-4 py-3 rounded-lg border ${cfg.bg} ${cfg.border}`}
              >
                <Icon size={14} className={`${cfg.color} shrink-0 mt-0.5`} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] font-bold tracking-wider ${cfg.color}`}>
                      {cfg.label}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#1e2235] border border-[#252840] text-ink-muted font-mono">
                      {entry.source}
                    </span>
                    <span className="text-[10px] text-ink-muted font-mono ml-auto">
                      {fmt(entry.timestamp)}
                    </span>
                  </div>
                  <p className="text-[12px] text-ink-primary mt-1 leading-snug">
                    {entry.message}
                  </p>
                  {entry.detail && (
                    <p className="text-[11px] text-ink-muted mt-1 font-mono break-all">
                      {entry.detail}
                    </p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
