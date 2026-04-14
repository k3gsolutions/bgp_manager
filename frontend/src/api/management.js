import { api } from './client.js'

export const managementApi = {
  exportBackup: () => api.get('/management/backup/export').then(r => r.data),
  importBackup: (data) => api.post('/management/backup/import', { data }).then(r => r.data),
  getUpdateStatus: () => api.get('/management/system-update/status').then(r => r.data),
  checkUpdate: () => api.post('/management/system-update/check').then(r => r.data),
  runUpdate: () => api.post('/management/system-update/run').then(r => r.data),
}
