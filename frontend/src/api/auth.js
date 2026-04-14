import { api } from './client.js'

export const authApi = {
  login: (username, password) =>
    api.post('/auth/login', { username, password }).then(r => r.data),
  me: () => api.get('/auth/me').then(r => r.data),
}
