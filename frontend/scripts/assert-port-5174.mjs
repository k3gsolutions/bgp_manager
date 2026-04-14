#!/usr/bin/env node
/**
 * Garante que a porta 5174 está livre antes do Vite (strictPort).
 * macOS/Linux: usa lsof para mostrar PID e comando de kill.
 */
import { execSync } from 'node:child_process'
import net from 'node:net'
import process from 'node:process'

const PORT = 5174

function listenCheck() {
  return new Promise((resolve) => {
    const s = net.createServer()
    s.once('error', (err) => {
      if (err.code === 'EADDRINUSE') resolve(false)
      else {
        console.warn('[assert-port] aviso ao testar porta:', err.message)
        resolve(true)
      }
    })
    s.once('listening', () => {
      s.close(() => resolve(true))
    })
    s.listen(PORT, '0.0.0.0')
  })
}

const free = await listenCheck()
if (free) process.exit(0)

console.error(`\n\x1b[31m✖ A porta ${PORT} já está em uso.\x1b[0m`)
console.error('Encerre o processo que está escutando nessa porta antes de rodar o frontend.\n')

try {
  const out = execSync(`lsof -nP -iTCP:${PORT} -sTCP:LISTEN 2>/dev/null`, { encoding: 'utf8' }).trim()
  if (out) {
    console.error('Escuta em TCP (LISTEN):\n')
    console.error(out)
    console.error('')
    const pids = new Set()
    for (const line of out.split('\n')) {
      const parts = line.trim().split(/\s+/)
      if (parts[1] === 'PID') continue
      const pid = parts[1]
      if (pid && /^\d+$/.test(pid)) pids.add(pid)
    }
    if (pids.size) {
      console.error('Para encerrar a sessão:')
      for (const p of pids) {
        console.error(`  kill ${p}     (ou kill -9 ${p} se não encerrar)`)
      }
    }
  } else {
    console.error('lsof não retornou linhas (permissões ou SO). Verifique manualmente:')
    console.error(`  lsof -nP -iTCP:${PORT} -sTCP:LISTEN`)
  }
} catch {
  console.error(`Comando sugerido: lsof -nP -iTCP:${PORT} -sTCP:LISTEN`)
}

console.error('')
process.exit(1)
