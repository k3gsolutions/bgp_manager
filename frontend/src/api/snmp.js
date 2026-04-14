import { api } from './client.js'

export const snmpApi = {
  collect:    (id) => api.post(`/devices/${id}/snmp/collect`).then(r => r.data),
  /** Só status IF + BGP (persiste no banco, sem inventário completo) */
  statusRefresh: (id) => api.post(`/devices/${id}/snmp/status-refresh`).then(r => r.data),
  inventoryHistory: (id, params = {}) =>
    api.get(`/devices/${id}/inventory-history`, { params }).then(r => r.data),
  interfaces: (id) => api.get(`/devices/${id}/interfaces`).then(r => r.data),
  bgpPeers:   (id) => api.get(`/devices/${id}/bgp-peers`).then(r => r.data),
  /** Huawei SSH: prefixos advertidos ao peer Operadora/IX/CDN (20/página; máx. 200 listadas). */
  bgpProviderAdvertisedRoutes: (deviceId, body) =>
    api.post(`/devices/${deviceId}/bgp/provider-advertised-routes`, body).then(r => r.data),
  /** Huawei SSH: prefixos recebidos do peer Cliente — received-routes (20/página; máx. 200). */
  bgpCustomerReceivedRoutes: (deviceId, body) =>
    api.post(`/devices/${deviceId}/bgp/customer-received-routes`, body).then(r => r.data),
  updatePeerRole: (deviceId, peerId, body) =>
    api.patch(`/devices/${deviceId}/bgp-peers/${peerId}`, body).then(r => r.data),
  deactivateInterface: (deviceId, interfaceId) =>
    api.patch(`/devices/${deviceId}/interfaces/${interfaceId}/deactivate`).then(r => r.data),
  deactivatePeer: (deviceId, peerId) =>
    api.patch(`/devices/${deviceId}/bgp-peers/${peerId}/deactivate`).then(r => r.data),
  /** Remove o registro do peer deste equipamento (DELETE 204). */
  deletePeer: (deviceId, peerId) =>
    api.delete(`/devices/${deviceId}/bgp-peers/${peerId}`).then(r => r.data),
  liveIfaces: (id) => api.get(`/devices/${id}/snmp/interfaces/live`).then(r => r.data),
  liveBgp:    (id) => api.get(`/devices/${id}/snmp/bgp/live`).then(r => r.data),
}
