import { useState, useCallback, useEffect, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { snmpApi } from './api/snmp.js'
import Sidebar from './components/Sidebar.jsx'
import Header from './components/Header.jsx'
import DeviceTree from './components/DeviceTree.jsx'
import DashboardPage from './pages/DashboardPage.jsx'
import DevicesPage from './pages/DevicesPage.jsx'
import InterfacesPanel from './pages/InterfacesPanel.jsx'
import BGPPanel from './pages/BGPPanel.jsx'
import BgpLookupPanel from './pages/BgpLookupPanel.jsx'
import FiltrosPanel from './pages/FiltrosPanel.jsx'
import LogPanel from './pages/LogPanel.jsx'
import LoginPage from './pages/LoginPage.jsx'
import CompaniesPage from './pages/CompaniesPage.jsx'
import UsersPage from './pages/UsersPage.jsx'
import ManagementPage from './pages/ManagementPage.jsx'
import { LogProvider } from './context/LogContext.jsx'
import { AuthProvider, useAuth } from './context/AuthContext.jsx'

const SNMP_FULL_MS = 5 * 60 * 1000
const SNMP_FULL_INITIAL_DELAY_MS = 900

function AppAuthenticated() {
  const { hasPermission } = useAuth()
  const [activePage, setActivePage] = useState('devices')
  const [deviceCount, setDeviceCount] = useState(null)
  const [devices, setDevices] = useState([])
  const [selected, setSelected] = useState(null)
  const [showModal, setShowModal] = useState(false)
  const [snmpPollTick, setSnmpPollTick] = useState(0)

  const handleDeviceCountChange = useCallback((count, deviceList) => {
    setDeviceCount(count)
    if (deviceList) setDevices(deviceList)
  }, [])

  const handleNavigate = useCallback((page) => {
    setActivePage(page)
    if (page !== 'devices') setSelected(null)
  }, [])

  const headerBreadcrumbItems = useMemo(() => {
    if (activePage === 'dashboard') {
      return [{ label: 'Admin' }, { label: 'Dashboard' }]
    }
    if (activePage === 'logs') {
      return [{ label: 'Admin' }, { label: 'Logs' }]
    }
    if (activePage === 'companies') {
      return [{ label: 'Admin' }, { label: 'Empresas' }]
    }
    if (activePage === 'users') {
      return [{ label: 'Admin' }, { label: 'Usuários' }]
    }
    if (activePage === 'devices') {
      if (!selected) {
        return [{ label: 'Admin' }, { label: 'Equipamentos' }]
      }
      const deviceLabel = selected.device.name || selected.device.ip_address
      const viewLabel = {
        interfaces: 'Interfaces',
        bgp: 'BGP',
        filtros: 'Filtros',
        lookup: 'Busca de Prefixo',
      }[selected.view] || ''
      const items = [
        { label: 'Admin' },
        { label: 'Equipamentos' },
        { label: deviceLabel },
      ]
      if (viewLabel) items.push({ label: viewLabel })
      return items
    }
    if (activePage === 'management') {
      return [{ label: 'Admin' }, { label: 'Gerenciamento' }]
    }
    return [{ label: 'Admin' }]
  }, [activePage, selected])

  const showTree = activePage === 'devices'
  const showDevicePanel = activePage === 'devices' && selected

  useEffect(() => {
    const d = selected?.device
    if (!showDevicePanel || !d?.snmp_community) return undefined

    let cancelled = false
    const bump = () => {
      if (!cancelled) setSnmpPollTick(t => t + 1)
    }

    const runFull = () => {
      snmpApi.collect(d.id).then(bump).catch(() => {})
    }

    const tInitial = window.setTimeout(() => {
      if (!cancelled) runFull()
    }, SNMP_FULL_INITIAL_DELAY_MS)

    const tFull = window.setInterval(runFull, SNMP_FULL_MS)

    return () => {
      cancelled = true
      window.clearTimeout(tInitial)
      window.clearInterval(tFull)
    }
  }, [showDevicePanel, selected?.device?.id, selected?.device?.snmp_community])

  return (
    <div className="flex min-h-screen bg-[#0f111a]">
      <Sidebar
        activePage={activePage}
        onNavigate={handleNavigate}
        deviceCount={deviceCount}
      />

      {showTree && (
        <DeviceTree
          devices={devices}
          selected={selected}
          onSelect={setSelected}
          onNewDevice={() => setShowModal(true)}
        />
      )}

      <div className="flex flex-col flex-1 min-w-0">
        <Header breadcrumbItems={headerBreadcrumbItems} />

        <main className="flex-1 p-6 overflow-y-auto">
          {activePage === 'dashboard' && (
            <DashboardPage onDeviceCountChange={handleDeviceCountChange} />
          )}

          {activePage === 'logs' && hasPermission('logs.view') && <LogPanel />}

          {activePage === 'companies' && hasPermission('companies.view') && <CompaniesPage />}

          {activePage === 'users' && hasPermission('users.view') && <UsersPage />}
          {activePage === 'management' && hasPermission('management.backup') && <ManagementPage />}

          {activePage === 'devices' && (
            <div className={showDevicePanel ? 'hidden' : ''}>
              <DevicesPage
                onDeviceCountChange={handleDeviceCountChange}
                showModal={showModal}
                onModalClose={() => setShowModal(false)}
              />
            </div>
          )}

          {showDevicePanel && selected.view === 'lookup' && (
            <BgpLookupPanel device={selected.device} />
          )}
          {showDevicePanel && selected.view === 'interfaces' && (
            <InterfacesPanel device={selected.device} snmpPollTick={snmpPollTick} />
          )}
          {showDevicePanel && selected.view === 'bgp' && (
            <BGPPanel device={selected.device} snmpPollTick={snmpPollTick} />
          )}
          {showDevicePanel && selected.view === 'filtros' && (
            <FiltrosPanel device={selected.device} />
          )}
        </main>
      </div>
    </div>
  )
}

function AppGate() {
  const { me, loading } = useAuth()
  if (loading) {
    return (
      <div className="min-h-screen bg-[#0f111a] flex items-center justify-center">
        <Loader2 className="animate-spin text-brand-blue" size={28} />
      </div>
    )
  }
  if (!me) {
    return <LoginPage />
  }
  return (
    <LogProvider>
      <AppAuthenticated />
    </LogProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  )
}
