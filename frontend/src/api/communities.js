import { api } from './client.js'

const base = (deviceId) => `/devices/${deviceId}`

export const communitiesApi = {
  library: (deviceId, params = {}) =>
    api.get(`${base(deviceId)}/communities/library`, { params }).then((r) => r.data),
  resyncFromConfig: (deviceId) =>
    api.post(`${base(deviceId)}/communities/resync-from-config`).then((r) => r.data),
  resyncLive: (deviceId) =>
    api.post(`${base(deviceId)}/communities/resync-live`).then((r) => r.data),
  listSets: (deviceId) => api.get(`${base(deviceId)}/community-sets`).then((r) => r.data),
  getSet: (deviceId, setId) => api.get(`${base(deviceId)}/community-sets/${setId}`).then((r) => r.data),
  createSet: (deviceId, body) => api.post(`${base(deviceId)}/community-sets`, body).then((r) => r.data),
  updateSet: (deviceId, setId, body) =>
    api.put(`${base(deviceId)}/community-sets/${setId}`, body).then((r) => r.data),
  deleteSet: (deviceId, setId) => api.delete(`${base(deviceId)}/community-sets/${setId}`),
  cloneSet: (deviceId, setId, body = {}) =>
    api.post(`${base(deviceId)}/community-sets/${setId}/clone`, body).then((r) => r.data),
  compareSets: (deviceId, body) =>
    api.post(`${base(deviceId)}/community-sets/compare`, body).then((r) => r.data),
  previewSet: (deviceId, setId) =>
    api.post(`${base(deviceId)}/community-sets/${setId}/preview`).then((r) => r.data),
  applySet: (deviceId, setId, body) =>
    api.post(`${base(deviceId)}/community-sets/${setId}/apply`, body).then((r) => r.data),
  setUsage: (deviceId, setId) =>
    api.get(`${base(deviceId)}/community-sets/${setId}/usage`).then((r) => r.data),
}
