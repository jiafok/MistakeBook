<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NCard, NForm, NFormItem, NInput, NSpace, NAlert, useMessage } from 'naive-ui'
import { useAuthStore } from '../stores/auth'
import { apiErrorMessage } from '../api/client'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const message = useMessage()

const mode = ref<'login' | 'register'>('login')
const identifier = ref('')
const password = ref('')
const displayName = ref('')
const loading = ref(false)
const errorText = ref('')

const isEmail = computed(() => identifier.value.includes('@'))
const identifierLabel = computed(() => (mode.value === 'login' ? '用户名或邮箱' : '用户名或邮箱'))
const identifierPlaceholder = computed(() =>
  mode.value === 'login' ? '输入用户名或邮箱' : '用户名（也可填写邮箱）',
)
const identifierHint = computed(() => {
  if (mode.value !== 'register') return ''
  if (!identifier.value.trim()) return '用户名 3–64 位，支持字母、数字、下划线、点和连字符'
  if (isEmail.value) return '将使用邮箱注册，用户名自动从邮箱前缀生成'
  return '将使用该用户名登录'
})

const canSubmit = computed(
  () => identifier.value.trim().length > 0 && password.value.length >= 6,
)

async function submit() {
  errorText.value = ''
  if (!identifier.value.trim()) {
    errorText.value = '请填写用户名或邮箱'
    return
  }
  if (password.value.length < 6) {
    errorText.value = '密码至少 6 位'
    return
  }
  loading.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(identifier.value.trim(), password.value)
    } else {
      await auth.register(identifier.value.trim(), password.value, displayName.value.trim())
    }
    auth.loadStatus(true).catch(() => undefined)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch (error) {
    errorText.value = apiErrorMessage(error)
    message.error(errorText.value)
  } finally {
    loading.value = false
  }
}

function switchMode(next: 'login' | 'register') {
  mode.value = next
  errorText.value = ''
}
</script>

<template>
  <div class="login-page">
    <div class="login-brand">
      <div class="brand-mark">MB</div>
      <h1>MistakeBook AI</h1>
      <p class="brand-tagline">拍照上传试卷，自动切题与推荐错题。<br />由你确认，才进入错题本。</p>
      <ul class="brand-points">
        <li>AI 只推荐，不做最终决定</li>
        <li>手机上可直接拍照上传</li>
        <li>每个账号数据相互隔离</li>
      </ul>
    </div>

    <NCard class="login-card" :bordered="false">
      <div class="mode-switch">
        <button
          class="mode-btn"
          :class="{ active: mode === 'login' }"
          type="button"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          class="mode-btn"
          :class="{ active: mode === 'register' }"
          type="button"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <NAlert v-if="errorText" type="error" :bordered="false" class="login-error">
        {{ errorText }}
      </NAlert>

      <NForm label-placement="top" class="login-form" @submit.prevent="submit">
        <NFormItem :label="identifierLabel">
          <NInput
            v-model:value="identifier"
            :placeholder="identifierPlaceholder"
            autocomplete="username"
            autocapitalize="none"
            autocorrect="off"
            spellcheck="false"
            inputmode="text"
            clearable
            size="large"
            @keyup.enter="submit"
          />
          <template #feedback>
            <span v-if="identifierHint" class="field-hint">{{ identifierHint }}</span>
          </template>
        </NFormItem>

        <NFormItem label="密码">
          <NInput
            v-model:value="password"
            type="password"
            show-password-on="click"
            placeholder="至少 6 位"
            autocomplete="current-password"
            size="large"
            @keyup.enter="submit"
          />
        </NFormItem>

        <NFormItem v-if="mode === 'register'" label="昵称（可选）">
          <NInput
            v-model:value="displayName"
            placeholder="例如：妈妈 / 小明"
            maxlength="120"
            size="large"
            @keyup.enter="submit"
          />
        </NFormItem>

        <NSpace vertical :size="12">
          <NButton
            type="primary"
            block
            size="large"
            :loading="loading"
            :disabled="!canSubmit"
            attr-type="submit"
          >
            {{ mode === 'login' ? '登录' : '创建账号' }}
          </NButton>
          <p class="login-tip">
            首次部署时注册的第一个账号为管理员，可查看其他账号的学习数据（只读）。
          </p>
        </NSpace>
      </NForm>
    </NCard>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  align-items: center;
  gap: clamp(24px, 5vw, 64px);
  padding: clamp(24px, 5vw, 72px);
  background: radial-gradient(1200px 600px at 8% 6%, #e8f0ff 0%, #f6f8fc 55%, #eef2f8 100%);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, #2563eb, #4f8cff);
  color: #fff;
  font-weight: 700;
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.28);
}

.login-brand h1 {
  font-size: clamp(26px, 3.2vw, 34px);
  margin: 16px 0 8px;
  letter-spacing: -0.5px;
}

.brand-tagline {
  color: #475569;
  line-height: 1.8;
  margin: 0 0 18px;
}

.brand-points {
  padding-left: 18px;
  color: #64748b;
  font-size: 13px;
  line-height: 2;
  margin: 0;
}

.login-card {
  max-width: 430px;
  width: 100%;
  border-radius: 18px;
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.1);
  justify-self: center;
}

.mode-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  padding: 4px;
  background: #eef2f7;
  border-radius: 12px;
}

.mode-btn {
  border: none;
  background: transparent;
  padding: 10px 0;
  border-radius: 9px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.mode-btn.active {
  background: #fff;
  color: var(--mb-brand);
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.1);
}

.login-error {
  margin-top: 14px;
  border-radius: 10px;
}

.login-form {
  margin-top: 16px;
}

.field-hint {
  font-size: 12px;
  color: var(--mb-muted);
  line-height: 1.6;
}

.login-tip {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
  line-height: 1.7;
}

/* ---- tablet / phone ---- */
@media (max-width: 920px) {
  .login-page {
    grid-template-columns: 1fr;
    justify-items: center;
    align-content: start;
    padding: 28px 20px 40px;
    gap: 20px;
  }

  .login-brand {
    text-align: center;
    width: 100%;
    max-width: 430px;
  }

  .brand-mark {
    margin: 0 auto;
  }

  .login-brand h1 {
    margin: 12px 0 6px;
  }

  .brand-points {
    display: none;
  }

  .brand-tagline {
    margin-bottom: 0;
  }

  .login-card {
    max-width: 430px;
  }
}

@media (max-width: 480px) {
  .login-page {
    padding: 18px 14px 32px;
  }

  .login-brand h1 {
    font-size: 24px;
  }

  .brand-tagline {
    font-size: 13px;
  }
}
</style>
