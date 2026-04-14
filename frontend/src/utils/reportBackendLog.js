/**
 * Envia para o painel Log o rastro `log[]` retornado pelo backend (detalhe em bloco monoespaçado).
 */
export function reportBackendLog(addLog, source, title, lines) {
  if (!Array.isArray(lines) || lines.length === 0) return
  addLog('info', source, title, lines.join('\n'))
}
