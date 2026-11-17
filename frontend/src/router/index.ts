import { createRouter, createWebHistory } from 'vue-router'
import { TOKEN_KEY } from '../api/client'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
    { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
    { path: '/upload', name: 'upload', component: () => import('../views/UploadView.vue') },
    { path: '/papers/:id(\\d+)/workspace', name: 'workspace', component: () => import('../views/WorkspaceView.vue') },
    { path: '/mistakes', name: 'mistakes', component: () => import('../views/MistakesView.vue') },
    { path: '/mistakes/:id(\\d+)', name: 'mistake-detail', component: () => import('../views/MistakeDetailView.vue') },
    { path: '/knowledge', name: 'knowledge', component: () => import('../views/KnowledgeView.vue') },
    { path: '/practice', name: 'practice', component: () => import('../views/PracticeView.vue') },
    { path: '/reviews', name: 'reviews', component: () => import('../views/ReviewsView.vue') },
    { path: '/analytics', name: 'analytics', component: () => import('../views/AnalyticsView.vue') },
    { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue') },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('../views/AdminView.vue'),
      meta: { adminOnly: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  const hasToken = Boolean(localStorage.getItem(TOKEN_KEY))
  if (!to.meta.public && !hasToken) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && hasToken) {
    return { name: 'dashboard' }
  }
  if (to.meta.adminOnly) {
    // Lazily imported so the router module stays free of store imports at setup time.
    const { useAuthStore } = await import('../stores/auth')
    const auth = useAuthStore()
    if (!auth.user) await auth.restore()
    if (auth.user?.role !== 'admin') return { name: 'dashboard' }
  }
  return true
})

export default router
