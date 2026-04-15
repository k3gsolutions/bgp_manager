import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import RootErrorBoundary from './RootErrorBoundary.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </React.StrictMode>,
)
