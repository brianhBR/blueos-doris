import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig(({ mode }) => {
  // Keyed off the explicit `--mode demo` rather than an env var: loadEnv
  // with an empty prefix reads all of process.env, so a stray VITE_DEMO_MODE
  // on a build machine must not be able to change the vehicle's base path.
  const isDemo = mode === 'demo'
  const env = isDemo ? loadEnv(mode, __dirname, '') : ({} as Record<string, string>)

  return {
    plugins: [vue()],
    // The vehicle serves the SPA from the origin root. Only the static demo
    // build needs a subpath, and getting this wrong ships a blank page to
    // the vehicle, so it stays pinned to '/' unless demo mode is explicit.
    base: isDemo ? env.DEMO_BASE || '/blueos-doris/' : '/',
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    server: {
      port: 3000,
      host: true,
      proxy: {
        // Local UI: forward API + recorder to DORIS backend (default BlueOS extension port)
        '/api': { target: 'http://127.0.0.1:8095', changeOrigin: true },
      },
    },
  }
})
