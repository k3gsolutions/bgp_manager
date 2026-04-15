import { Component } from 'react'

/**
 * Evita tela em branco silenciosa: exibe mensagem quando algum render quebra.
 */
export default class RootErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  render() {
    const { error } = this.state
    if (error) {
      const msg = error?.message || String(error)
      return (
        <div
          style={{
            minHeight: '100vh',
            background: '#0f111a',
            color: '#fca5a5',
            padding: 32,
            fontFamily: 'system-ui, sans-serif',
            fontSize: 14,
          }}
        >
          <h1 style={{ color: '#f1f5f9', fontSize: 18, marginBottom: 12 }}>
            Erro ao carregar o frontend
          </h1>
          <p style={{ color: '#94a3b8', marginBottom: 16 }}>
            Corrija o erro abaixo ou recarregue a página. Se persistir, abra o console do navegador (F12).
          </p>
          <pre
            style={{
              whiteSpace: 'pre-wrap',
              background: '#11141f',
              border: '1px solid #252840',
              borderRadius: 8,
              padding: 16,
              fontSize: 12,
              color: '#e2e8f0',
              overflow: 'auto',
            }}
          >
            {msg}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}
