import { api } from './client.js'

/** Timeouts curtos: evitam spinner infinito no bootstrap se o backend não responder. */
const LOGIN_TIMEOUT_MS = 30_000
const ME_TIMEOUT_MS = 15_000

export const authApi = {
  login: (username, password) =>
    api
      .post('/auth/login', { username, password }, { timeout: LOGIN_TIMEOUT_MS })
      .then(r => r.data),
  me: () => api.get('/auth/me', { timeout: ME_TIMEOUT_MS }).then(r => r.data),
}
