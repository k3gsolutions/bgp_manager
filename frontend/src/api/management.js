import { api } from './client.js'

export const managementApi = {
  exportBackup: () => api.get('/management/backup/export').then(r => r.data),
  importBackup: (data) => api.post('/management/backup/import', { data }).then(r => r.data),
  // Updates remotos (versão Docker versionada via GitHub Releases/GHCR).
  getSystemVersion: () => api.get('/system/version').then(r => r.data),
  getUpdateStatus: () => api.get('/system/update-status').then(r => r.data),
  checkUpdate: () => api.post('/system/check-update').then(r => r.data),
  applyUpdate: (payload) => api.post('/system/apply-update', payload).then(r => r.data),
  rollbackUpdate: (payload) => api.post('/system/rollback-update', payload).then(r => r.data),
  getUpdateHistory: (limit = 20) => api.get('/system/update-history', { params: { limit } }).then(r => r.data),
}
