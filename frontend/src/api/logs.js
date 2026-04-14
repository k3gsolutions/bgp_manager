import { api } from './client.js'

export const logsApi = {
  recent: (limit = 100) =>
    api.get('/logs/recent', { params: { limit } }).then(r => r.data),
}
