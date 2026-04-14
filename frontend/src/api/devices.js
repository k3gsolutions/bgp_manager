import { api } from './client.js'

export const devicesApi = {
  list: () => api.get('/devices/').then(r => r.data),
  get: (id) => api.get(`/devices/${id}`).then(r => r.data),
  create: (data) => api.post('/devices/', data).then(r => r.data),
  /** Importação em lote (mesmo formato que create, por item). */
  batchCreate: (devices) => api.post('/devices/batch', { devices }).then(r => r.data),
  update: (id, data) => api.put(`/devices/${id}`, data).then(r => r.data),
  remove: (id) => api.delete(`/devices/${id}`),
  testConnection: (id) => api.post(`/devices/${id}/test-connection`).then(r => r.data),
  /** Huawei VRP — mesma lógica de coleta do netops_netbox_sync (display *, BGP em todas as VRFs). */
  sshCollectHuawei: (id, opts) =>
    api
      .post(`/devices/${id}/ssh/collect-huawei`, null, {
        params: { purge_inactive_bgp_first: Boolean(opts?.purgeInactiveBgpFirst) },
      })
      .then(r => r.data),
  /** Remove apenas linhas BGP com is_active=false para este equipamento. */
  purgeInactiveBgpPeers: (id) =>
    api.post(`/devices/${id}/maintenance/purge-inactive-bgp-peers`).then(r => r.data),
  /** Huawei VRP — display bgp routing-table + advertised-routes por operadora */
  bgpExportLookup: (id, body) =>
    api.post(`/devices/${id}/ssh/bgp-export-lookup`, body).then(r => r.data),
}
