import { api } from './client.js'

export const usersApi = {
  list: () => api.get('/users/').then(r => r.data),
  create: body => api.post('/users/', body).then(r => r.data),
  update: (id, body) => api.put(`/users/${id}`, body).then(r => r.data),
  remove: id => api.delete(`/users/${id}`),
  patchCompanies: (id, company_ids) =>
    api.patch(`/users/${id}/companies`, { company_ids }).then(r => r.data),
  patchPassword: (id, password) =>
    api.patch(`/users/${id}/password`, { password }).then(r => r.data),
}
