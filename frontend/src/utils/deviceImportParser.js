/**
 * Parser de importação em lote (CSV / XML) → payloads compatíveis com POST /api/devices/batch.
 * Campos obrigatórios por linha: company_id (ou company_name), ip_address, username, password.
 */

const HEADER_ALIASES = {
  company_id: 'company_id',
  id_empresa: 'company_id',
  empresa_id: 'company_id',
  company_name: 'company_name',
  nome_empresa: 'company_name',
  empresa: 'company_name',
  client: 'client',
  rotulo: 'client',
  name: 'name',
  nome: 'name',
  hostname: 'name',
  ip_address: 'ip_address',
  ip: 'ip_address',
  endereco_ip: 'ip_address',
  ssh_port: 'ssh_port',
  porta_ssh: 'ssh_port',
  port: 'ssh_port',
  vendor: 'vendor',
  fabricante: 'vendor',
  model: 'model',
  modelo: 'model',
  username: 'username',
  usuario: 'username',
  user: 'username',
  password: 'password',
  senha: 'password',
  snmp_community: 'snmp_community',
  snmp: 'snmp_community',
  community: 'snmp_community',
  description: 'description',
  descricao: 'description',
}

function normHeader(h) {
  return String(h || '')
    .trim()
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/\s+/g, '_')
}

/** RFC4180-ish: vírgulas dentro de aspas, "" como escape. */
export function parseCsvLine(line) {
  const out = []
  let cur = ''
  let inQ = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (inQ) {
      if (c === '"') {
        if (line[i + 1] === '"') {
          cur += '"'
          i++
        } else {
          inQ = false
        }
      } else {
        cur += c
      }
    } else if (c === '"') {
      inQ = true
    } else if (c === ',') {
      out.push(cur.trim())
      cur = ''
    } else {
      cur += c
    }
  }
  out.push(cur.trim())
  return out
}

function buildCompanyNameMap(companies) {
  const m = new Map()
  for (const c of companies || []) {
    if (c?.name) m.set(String(c.name).trim().toLowerCase(), c.id)
  }
  return m
}

function emptyToNull(v) {
  if (v === undefined || v === null) return null
  const s = String(v).trim()
  return s === '' ? null : s
}

function coerceDeviceRow(obj, companyByName) {
  const o = { ...obj }
  const cn = emptyToNull(o.company_name)
  const cidRaw = o.company_id
  let company_id = cidRaw !== undefined && cidRaw !== '' && cidRaw !== null ? Number(cidRaw) : NaN
  if (!Number.isFinite(company_id) && cn) {
    const idFromName = companyByName.get(String(cn).toLowerCase())
    if (idFromName != null) company_id = idFromName
  }
  const ssh = o.ssh_port === undefined || o.ssh_port === '' || o.ssh_port === null ? 22 : Number(o.ssh_port)
  return {
    company_id: Number.isFinite(company_id) ? company_id : null,
    client: emptyToNull(o.client),
    name: emptyToNull(o.name),
    ip_address: String(o.ip_address || '').trim(),
    ssh_port: Number.isFinite(ssh) ? ssh : 22,
    vendor: String(o.vendor || 'Huawei').trim() || 'Huawei',
    model: emptyToNull(o.model),
    username: String(o.username || '').trim(),
    password: String(o.password ?? ''),
    snmp_community: emptyToNull(o.snmp_community),
    description: emptyToNull(o.description),
  }
}

function validatePayloadForPreview(row, idx) {
  const errs = []
  if (row.company_id == null || !Number.isFinite(row.company_id)) {
    errs.push(`Linha ${idx + 1}: company_id inválido ou company_name não encontrado`)
  }
  if (!row.ip_address) errs.push(`Linha ${idx + 1}: ip_address obrigatório`)
  if (!row.username) errs.push(`Linha ${idx + 1}: username obrigatório`)
  if (!row.password) errs.push(`Linha ${idx + 1}: password obrigatório`)
  if (row.ssh_port < 1 || row.ssh_port > 65535) errs.push(`Linha ${idx + 1}: ssh_port inválido`)
  return errs
}

/**
 * @param {string} text
 * @param {{ id: number, name: string }[]} companies
 * @returns {{ devices: object[], parseErrors: string[], rowErrors: string[] }}
 */
export function parseDeviceImportCsv(text, companies) {
  const parseErrors = []
  const lines = String(text || '')
    .replace(/^\uFEFF/, '')
    .split(/\r?\n/)
    .map(l => l.trimEnd())
    .filter((l, i) => l.length > 0 || i === 0)

  if (!lines.length) {
    parseErrors.push('Ficheiro CSV vazio.')
    return { devices: [], parseErrors, rowErrors: [] }
  }

  const headers = parseCsvLine(lines[0]).map(h => HEADER_ALIASES[normHeader(h)] || normHeader(h))
  const devices = []
  const companyByName = buildCompanyNameMap(companies)

  for (let r = 1; r < lines.length; r++) {
    const cells = parseCsvLine(lines[r])
    if (cells.every(c => c === '')) continue
    const obj = {}
    headers.forEach((key, i) => {
      if (!key) return
      obj[key] = cells[i] !== undefined ? cells[i] : ''
    })
    devices.push(coerceDeviceRow(obj, companyByName))
  }

  if (!devices.length) parseErrors.push('Nenhuma linha de dados após o cabeçalho.')

  const rowErrors = []
  devices.forEach((d, i) => rowErrors.push(...validatePayloadForPreview(d, i)))
  return { devices, parseErrors, rowErrors }
}

/**
 * @param {string} text
 * @param {{ id: number, name: string }[]} companies
 */
export function parseDeviceImportXml(text, companies) {
  const parseErrors = []
  let doc
  try {
    doc = new DOMParser().parseFromString(String(text || ''), 'text/xml')
  } catch {
    parseErrors.push('XML inválido (parser).')
    return { devices: [], parseErrors, rowErrors: [] }
  }
  const pe = doc.querySelector('parsererror')
  if (pe) {
    parseErrors.push('XML mal formado.')
    return { devices: [], parseErrors, rowErrors: [] }
  }

  let list = []
  const devicesRoot = doc.querySelector('devices')
  if (devicesRoot) {
    list = [...devicesRoot.children].filter(el => el.tagName && el.tagName.toLowerCase() === 'device')
  } else if (doc.documentElement && doc.documentElement.tagName.toLowerCase() === 'device') {
    list = [doc.documentElement]
  }

  if (!list.length) {
    parseErrors.push(
      'Nenhum elemento <device> encontrado. Envolva os equipamentos em <devices>…</devices> (ver modelo XML).',
    )
    return { devices: [], parseErrors, rowErrors: [] }
  }

  const companyByName = buildCompanyNameMap(companies)
  const tagNames = [
    'company_id',
    'company_name',
    'client',
    'name',
    'ip_address',
    'ssh_port',
    'vendor',
    'model',
    'username',
    'password',
    'snmp_community',
    'description',
  ]

  function textOf(el, tag) {
    const c = el.getElementsByTagName(tag)[0]
    return c ? (c.textContent ?? '').trim() : ''
  }

  const devices = list.map(el => {
    const obj = {}
    for (const t of tagNames) obj[t] = textOf(el, t)
    return coerceDeviceRow(obj, companyByName)
  })

  const rowErrors = []
  devices.forEach((d, i) => rowErrors.push(...validatePayloadForPreview(d, i)))
  return { devices, parseErrors, rowErrors }
}

/**
 * @param {File} file
 * @param {string} text
 * @param {{ id: number, name: string }[]} companies
 */
export function parseDeviceImportFile(file, text, companies) {
  const name = (file?.name || '').toLowerCase()
  const sniff = String(text || '').trimStart().startsWith('<')
  if (name.endsWith('.xml') || sniff) return parseDeviceImportXml(text, companies)
  return parseDeviceImportCsv(text, companies)
}
