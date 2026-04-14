import { api } from './client.js'

export const managementApi = {
  exportBackup: () => api.get('/management/backup/export').then(r => r.data),
  importBackup: (data) => api.post('/management/backup/import', { data }).then(r => r.data),
}
