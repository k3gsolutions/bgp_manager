/**
 * Segmenta um AS-PATH (saída Huawei / API) por espaços e mostra cada token num
 * quadro alinhado ao estilo das communities (Badge roxo; AS local em azul).
 */

function stripOriginSuffix(tok) {
  return String(tok).replace(/[a-z?]$/i, '')
}

function isLocalAsToken(tok, localAsn) {
  if (localAsn == null || localAsn === '') return false
  const base = stripOriginSuffix(tok)
  return base === String(localAsn)
}

export function AsPathTokens({ asPath, localAsn, compact = false }) {
  const s = asPath == null ? '' : String(asPath).trim()
  if (!s) return <span className="text-ink-muted">—</span>

  const tokens = s.split(/\s+/).filter(Boolean)
  const pad = compact ? 'px-1.5 py-px text-[10px]' : 'px-2 py-0.5 text-[11px]'
  const purple = `inline-flex items-center rounded border font-semibold font-mono ${pad} bg-purple-500/10 border-purple-500/25 text-purple-300`
  const blue = `inline-flex items-center rounded border font-semibold font-mono ${pad} bg-blue-500/15 border-blue-500/30 text-blue-300`

  return (
    <ul className="flex flex-wrap items-center gap-1.5 list-none m-0 p-0" aria-label="AS-Path">
      {tokens.map((tok, i) => (
        <li key={`${i}-${tok}`} className="m-0 p-0">
          <span className={isLocalAsToken(tok, localAsn) ? blue : purple}>{tok}</span>
        </li>
      ))}
    </ul>
  )
}
