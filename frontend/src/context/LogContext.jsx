import { createContext, useContext, useState, useCallback } from 'react'

const LogContext = createContext(null)

let _id = 0

export function LogProvider({ children }) {
  const [entries, setEntries] = useState([])
  const [unread, setUnread] = useState(0)

  const addLog = useCallback((level, source, message, detail = null) => {
    const entry = {
      id: ++_id,
      timestamp: new Date(),
      level,   // 'error' | 'warn' | 'info' | 'success'
      source,  // ex: 'SSH', 'SNMP', 'API'
      message,
      detail,
    }
    setEntries(prev => [entry, ...prev].slice(0, 500))
    if (level === 'error' || level === 'warn') {
      setUnread(n => n + 1)
    }
  }, [])

  const clearUnread = useCallback(() => setUnread(0), [])
  const clearAll    = useCallback(() => setEntries([]), [])

  return (
    <LogContext.Provider value={{ entries, unread, addLog, clearUnread, clearAll }}>
      {children}
    </LogContext.Provider>
  )
}

export function useLog() {
  return useContext(LogContext)
}
