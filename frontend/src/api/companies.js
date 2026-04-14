import { api } from './client.js'

export const companiesApi = {
  list: () => api.get('/companies/').then(r => r.data),
  create: body => api.post('/companies/', body).then(r => r.data),
  update: (id, body) => api.put(`/companies/${id}`, body).then(r => r.data),
  remove: id => api.delete(`/companies/${id}`),
}
