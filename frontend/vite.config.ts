import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `npm run dev` is only for working on the UI: it proxies the API to the
// running ClipForge server. Normal use loads the built files from that server.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', ws: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
