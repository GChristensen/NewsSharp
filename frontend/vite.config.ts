// Vite configuration (frontend/vite.config.ts). In development the Vite server proxies the backend
// routes to Django; the build is written to frontend/dist and served by whitenoise.
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const backend = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(
      ['/api', '/img', '/push', '/admin', '/static'].map((path) => [path, { target: backend, changeOrigin: false }]),
    ),
  },
  test: { environment: 'node' },
})
