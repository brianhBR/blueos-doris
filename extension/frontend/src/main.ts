import { createApp } from 'vue'
import '@fontsource/montserrat/400.css'
import '@fontsource/montserrat/500.css'
import '@fontsource/montserrat/600.css'
import '@fontsource/montserrat/700.css'
import App from './App.vue'
import './styles/main.css'

// Vite inlines VITE_DEMO_MODE as a literal, so this branch and the whole
// ./demo module tree are dropped from the production bundle.
async function bootstrap() {
  if (import.meta.env.VITE_DEMO_MODE === 'true') {
    const { installDemoMode } = await import('./demo')
    installDemoMode()
  }
  createApp(App).mount('#app')
}

bootstrap()

