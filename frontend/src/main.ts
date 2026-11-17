import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import App from './App.vue'
import router from './router'
import './styles.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(naive)
app.mount('#app')

// ---------------------------------------------------------------------------
// PWA service worker
// ---------------------------------------------------------------------------
// Registered only in production builds: during `vite dev` a service worker
// would cache stale modules and make HMR behave unpredictably.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => {
      // Non-fatal: the app works fine without offline support.
      console.warn('[pwa] service worker registration failed', err)
    })
  })
}
