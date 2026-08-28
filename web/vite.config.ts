import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Local chat must not use :8010 — a stale process can keep that port
// and serve old refusals. Dev traffic goes same-origin, then here.
const FAQ_API = 'http://127.0.0.1:8011'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/v1': { target: FAQ_API, changeOrigin: true },
      '/health': { target: FAQ_API, changeOrigin: true },
      '/latency': { target: FAQ_API, changeOrigin: true },
    },
  },
})
