import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import LatencyPage from './Latency.tsx'

function Root() {
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (path === '/latency') {
    return <LatencyPage />
  }
  return <App />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
