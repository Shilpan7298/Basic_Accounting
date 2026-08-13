import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is same-origin in production; in dev it lives on :8000.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
