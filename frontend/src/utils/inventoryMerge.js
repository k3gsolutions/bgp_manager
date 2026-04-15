/**
 * Atualiza lista vinda do BD quando o conjunto de IDs é o mesmo (só mudaram campos).
 * Se houve adição/remoção de linhas, devolve `next` por completo (evita estado inconsistente).
 *
 * @template {{ id: number }} T
 * @param {T[]} prev
 * @param {T[]} next
 * @returns {T[]}
 */
export function mergeByStableId(prev, next) {
  if (!prev?.length) return next || []
  if (!next?.length) return next || []
  const prevIds = new Set(prev.map(r => r.id))
  const nextIds = new Set(next.map(r => r.id))
  const sameSize = prevIds.size === nextIds.size
  const sameSet = sameSize && [...nextIds].every(id => prevIds.has(id))
  if (!sameSet) return next
  const byId = new Map(next.map(r => [r.id, r]))
  return prev.map(row => ({ ...row, ...byId.get(row.id) }))
}
