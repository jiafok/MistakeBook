<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import {
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NMenu,
  NButton,
  NDrawer,
  NDrawerContent,
  NSpace,
  NTag,
  NText,
  NMessageProvider,
  NDialogProvider,
} from 'naive-ui'
import type { MenuOption } from 'naive-ui'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const isPublic = computed(() => Boolean(route.meta.public))
const isAdmin = computed(() => auth.user?.role === 'admin')
const drawerOpen = ref(false)

// Breakpoint handled in JS rather than CSS: NaiveUI's own `.n-menu` rules can
// outrank a scoped `display: none`, leaving both menus rendered on phones.
const MOBILE_QUERY = '(max-width: 980px)'
const isMobile = ref(
  typeof window !== 'undefined' ? window.matchMedia(MOBILE_QUERY).matches : false,
)

if (typeof window !== 'undefined') {
  const media = window.matchMedia(MOBILE_QUERY)
  const sync = (event: MediaQueryListEvent | MediaQueryList) => {
    isMobile.value = event.matches
    if (!isMobile.value) drawerOpen.value = false
  }
  media.addEventListener('change', sync)
}

const baseOptions: MenuOption[] = [
  { label: '首页', key: 'dashboard' },
  { label: '上传试卷', key: 'upload' },
  { label: '错题库', key: 'mistakes' },
  { label: '知识点', key: 'knowledge' },
  { label: 'AI 练习', key: 'practice' },
  { label: '复习计划', key: 'reviews' },
  { label: '数据统计', key: 'analytics' },
  { label: '设置', key: 'settings' },
]

// The oversight entry only exists for administrators.
const menuOptions = computed<MenuOption[]>(() =>
  isAdmin.value ? [...baseOptions, { label: '账号监管', key: 'admin' }] : baseOptions,
)

const activeKey = computed(() => {
  const name = String(route.name || 'dashboard')
  if (name === 'mistake-detail') return 'mistakes'
  if (name === 'workspace') return 'upload'
  return name
})

const displayName = computed(
  () => auth.user?.display_name || auth.user?.username || auth.user?.email || '',
)

function onMenu(key: string) {
  drawerOpen.value = false
  router.push({ name: key })
}

function logout() {
  drawerOpen.value = false
  auth.logout()
  router.push('/login')
}

// Close the mobile drawer whenever navigation happens.
watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  },
)

onMounted(async () => {
  if (localStorage.getItem('mistakebook.token')) {
    await auth.restore()
    auth.loadStatus().catch(() => undefined)
  }
})
</script>

<template>
  <NMessageProvider>
    <NDialogProvider>
      <RouterView v-if="isPublic" />
      <NLayout v-else position="absolute">
        <NLayoutHeader bordered class="app-header">
          <NButton
            v-if="isMobile"
            class="hamburger"
            quaternary
            circle
            aria-label="菜单"
            @click="drawerOpen = true"
          >
            <span class="bars"><i /><i /><i /></span>
          </NButton>

          <div class="brand">
            <span class="brand-mark">MB</span>
            <div>
              <div class="brand-name">MistakeBook AI</div>
              <div class="brand-desc">AI 推荐 · 你来决定</div>
            </div>
          </div>

          <NMenu
            v-if="!isMobile"
            class="desktop-menu"
            mode="horizontal"
            :options="menuOptions"
            :value="activeKey"
            responsive
            @update:value="onMenu"
          />

          <div class="spacer" />

          <NSpace align="center" :size="10" :wrap="false">
            <NTag v-if="auth.status" size="small" round :bordered="false" type="info">
              {{ auth.status.ai_available ? 'AI 已连接' : 'AI 未配置' }}
            </NTag>
            <NText depth="3" class="user-name">{{ displayName }}</NText>
            <NButton size="small" quaternary @click="logout">退出</NButton>
          </NSpace>
        </NLayoutHeader>

        <NLayoutContent class="app-content">
          <RouterView />
        </NLayoutContent>

        <NDrawer v-model:show="drawerOpen" placement="left" :width="264">
          <NDrawerContent title="MistakeBook AI" closable>
            <NMenu :options="menuOptions" :value="activeKey" @update:value="onMenu" />
            <template #footer>
              <NSpace vertical :size="10">
                <NText depth="3" style="font-size: 12px">{{ displayName }}</NText>
                <NButton size="small" secondary block @click="logout">退出登录</NButton>
              </NSpace>
            </template>
          </NDrawerContent>
        </NDrawer>
      </NLayout>
    </NDialogProvider>
  </NMessageProvider>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
  /* respect notches on iOS */
  padding-left: max(20px, env(safe-area-inset-left));
  padding-right: max(20px, env(safe-area-inset-right));
  height: 64px;
  background: var(--mb-panel);
}

.bars {
  display: grid;
  gap: 3px;
}

.bars i {
  display: block;
  width: 16px;
  height: 2px;
  border-radius: 1px;
  background: currentColor;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 176px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, #2563eb, #4f8cff);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
}

.brand-name {
  font-weight: 650;
  font-size: 14px;
  line-height: 1.2;
}

.brand-desc {
  font-size: 11px;
  color: var(--mb-muted);
  line-height: 1.2;
}

.spacer {
  flex: 1;
}

.app-content {
  background: var(--mb-bg);
  height: calc(100% - 64px);
  overflow: auto;
  -webkit-overflow-scrolling: touch;
}

@media (max-width: 980px) {
  .app-header {
    height: auto;
    padding: 10px 14px;
    padding-left: max(14px, env(safe-area-inset-left));
    padding-right: max(14px, env(safe-area-inset-right));
    gap: 10px;
  }
  .brand {
    min-width: 0;
  }
  .brand-desc {
    display: none;
  }
  .user-name {
    display: none;
  }
  .app-content {
    height: auto;
    min-height: calc(100dvh - 58px);
  }
}
</style>
