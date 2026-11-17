<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { AiSettings, AiTestResult, SystemStatus } from '../api/ai-settings-types'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const message = useMessage()

const status = ref<SystemStatus | null>(null)
const ai = ref<AiSettings | null>(null)
const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const testResult = ref<AiTestResult | null>(null)

const provider = ref('none')
const apiKey = ref('')
const baseUrl = ref('')
const model = ref('')
const timeoutSeconds = ref(60)

const isAdmin = computed(() => auth.user?.role === 'admin')

// ---- profile / password ----
const displayName = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const savingProfile = ref(false)
const savingPassword = ref(false)

// ---- PWA install prompt ----
type InstallPromptEvent = Event & { prompt: () => Promise<void> }
const installEvent = ref<InstallPromptEvent | null>(null)
const isStandalone = ref(false)

const canInstall = computed(() => installEvent.value !== null && !isStandalone.value)

async function promptInstall() {
  const event = installEvent.value
  if (!event) return
  await event.prompt()
  installEvent.value = null
}

const providerOptions = computed(() =>
  (ai.value?.providers ?? []).map((item) => ({ label: item.label, value: item.value })),
)

const currentProvider = computed(() =>
  (ai.value?.providers ?? []).find((item) => item.value === provider.value),
)

const needsKey = computed(() => currentProvider.value?.needs_key === 'true')
const isOllama = computed(() => provider.value === 'ollama')
const enabled = computed(() => provider.value !== 'none')

const keyPlaceholder = computed(() => {
  if (!needsKey.value) return '该提供方不需要 API Key'
  if (ai.value?.has_api_key) {
    return `已保存：${ai.value.api_key_masked}（留空则不修改）`
  }
  return '粘贴 API Key'
})

const modelPlaceholder = computed(
  () => ai.value?.default_models?.[provider.value] ?? '例如 gpt-4o-mini',
)

function applySettings(data: AiSettings) {
  ai.value = data
  provider.value = data.provider || 'none'
  baseUrl.value = data.base_url || ''
  model.value = data.model || ''
  timeoutSeconds.value = data.timeout_seconds || 60
  apiKey.value = ''
}

async function load() {
  loading.value = true
  try {
    const [statusData, aiData] = await Promise.all([
      api.status(),
      api.getAiSettings().catch(() => null),
    ])
    status.value = statusData
    auth.status = statusData
    if (aiData) applySettings(aiData)
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

// clear stale test results and fill the default model when switching provider
watch(provider, (next, previous) => {
  if (next === previous) return
  testResult.value = null
  const wasDefault = !model.value || model.value === ai.value?.default_models?.[previous]
  if (wasDefault) {
    model.value = ai.value?.default_models?.[next] ?? ''
  }
})

function buildPayload() {
  return {
    provider: provider.value,
    // undefined => leave unchanged; empty string => clear
    api_key: needsKey.value ? apiKey.value || undefined : undefined,
    base_url: baseUrl.value || (isOllama.value ? undefined : ''),
    model: model.value || '',
    timeout_seconds: timeoutSeconds.value,
  }
}

async function save() {
  if (!isAdmin.value) {
    message.warning('仅管理员可以修改实例设置')
    return
  }
  saving.value = true
  try {
    const payload = buildPayload()
    // For Ollama an empty base_url is invalid; send whatever the user typed.
    const data = await api.saveAiSettings(
      isOllama.value ? { ...payload, base_url: baseUrl.value || undefined } : payload,
    )
    applySettings(data)
    message.success('AI 配置已保存')
    status.value = await api.status()
    auth.status = status.value
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResult.value = null
  try {
    const result = await api.testAiSettings({
      provider: provider.value,
      api_key: needsKey.value ? apiKey.value || undefined : undefined,
      base_url: baseUrl.value || undefined,
      model: model.value || undefined,
      timeout_seconds: timeoutSeconds.value,
    })
    testResult.value = result
    if (result.ok) message.success('连接成功')
    else message.warning('连接失败，请检查配置')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    testing.value = false
  }
}

async function clearKey() {
  apiKey.value = ''
  if (!ai.value?.has_api_key) return
  saving.value = true
  try {
    const data = await api.saveAiSettings({ provider: provider.value, api_key: '' })
    applySettings(data)
    message.success('已清除保存的 API Key')
    status.value = await api.status()
    auth.status = status.value
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    saving.value = false
  }
}

async function saveProfile() {
  const name = displayName.value.trim()
  if (!name) {
    message.warning('昵称不能为空')
    return
  }
  savingProfile.value = true
  try {
    const user = await api.updateProfile({ display_name: name })
    auth.user = user
    message.success('个人资料已更新')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    savingProfile.value = false
  }
}

async function changePassword() {
  if (!currentPassword.value) {
    message.warning('请输入当前密码')
    return
  }
  if (newPassword.value.length < 8) {
    message.warning('新密码至少 8 位')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    message.warning('两次输入的新密码不一致')
    return
  }
  savingPassword.value = true
  try {
    const user = await api.updateProfile({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    auth.user = user
    currentPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    message.success('密码已修改，其他设备需重新登录')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    savingPassword.value = false
  }
}

function detectInstallState() {
  isStandalone.value =
    window.matchMedia('(display-mode: standalone)').matches ||
    // iOS Safari exposes this non-standard flag
    Boolean((window.navigator as unknown as { standalone?: boolean }).standalone)
}

onMounted(() => {
  displayName.value = auth.user?.display_name || ''
  detectInstallState()
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    installEvent.value = event as InstallPromptEvent
  })
  window.addEventListener('appinstalled', () => {
    installEvent.value = null
    isStandalone.value = true
    message.success('已安装到桌面')
  })
  void load()
})
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">设置</h2>
        <p class="mb-page-subtitle">AI 提供方可直接在此配置，保存后立即生效，无需重启容器。</p>
      </div>
      <NButton @click="load">刷新</NButton>
    </div>

    <NSpin :show="loading">
      <!-- ================= AI 配置（主区域） ================= -->
      <NCard :bordered="false" class="card" title="AI 配置">
        <template #header-extra>
          <NSpace :size="6">
            <NTag
              size="small"
              :type="ai?.configured ? 'success' : 'default'"
              :bordered="false"
            >
              {{ ai?.configured ? '已启用' : '未启用' }}
            </NTag>
            <NTag v-if="ai?.api_key_source === 'env'" size="small" :bordered="false" type="info">
              来自环境变量
            </NTag>
          </NSpace>
        </template>

        <NAlert v-if="!isAdmin" type="info" :bordered="false" style="margin-bottom: 14px">
          当前账号为普通成员，可查看配置但不能修改。请让管理员（首个注册账号）来调整。
        </NAlert>

        <NForm label-placement="top" :disabled="!isAdmin">
          <div class="form-grid">
            <NFormItem label="提供方">
              <NSelect v-model:value="provider" :options="providerOptions" />
            </NFormItem>

            <NFormItem label="模型">
              <NInput
                v-model:value="model"
                :placeholder="modelPlaceholder"
                :disabled="!enabled"
              />
            </NFormItem>
          </div>

          <NFormItem v-if="needsKey" label="API Key">
            <NSpace vertical style="width: 100%">
              <NInput
                v-model:value="apiKey"
                type="password"
                show-password-on="click"
                :placeholder="keyPlaceholder"
              />
              <div class="field-hint">
                密钥加密保存在本机数据库中，接口永不回传明文。
                <NButton
                  v-if="ai?.has_api_key"
                  text
                  type="error"
                  size="tiny"
                  style="margin-left: 6px"
                  @click="clearKey"
                >
                  清除已保存的密钥
                </NButton>
              </div>
            </NSpace>
          </NFormItem>

          <NFormItem
            :label="isOllama ? 'Ollama 服务地址（必填）' : '自定义网关地址（可选）'"
          >
            <NInput
              v-model:value="baseUrl"
              :placeholder="
                isOllama ? 'http://host.docker.internal:11434' : '留空使用官方地址'
              "
              :disabled="!enabled"
            />
          </NFormItem>

          <NFormItem label="超时（秒）">
            <NInputNumber v-model:value="timeoutSeconds" :min="5" :max="300" :disabled="!enabled" />
          </NFormItem>

          <div class="actions">
            <NButton
              type="primary"
              :loading="saving"
              :disabled="!isAdmin"
              @click="save"
            >
              保存配置
            </NButton>
            <NButton
              :loading="testing"
              :disabled="provider === 'none'"
              @click="testConnection"
            >
              测试连接
            </NButton>
          </div>
        </NForm>

        <NAlert
          v-if="testResult"
          :type="testResult.ok ? 'success' : 'error'"
          :bordered="false"
          style="margin-top: 14px"
          closable
          @close="testResult = null"
        >
          <div class="test-line">
            <strong>{{ testResult.ok ? '连接成功' : '连接失败' }}</strong>
            <span v-if="testResult.ok">· {{ testResult.latency_ms }} ms</span>
            <span>· {{ testResult.provider }} / {{ testResult.model || '默认模型' }}</span>
          </div>
          <div class="test-msg">{{ testResult.message }}</div>
        </NAlert>

        <NAlert v-if="!enabled" type="info" :bordered="false" style="margin-top: 14px">
          未启用 AI 时，错题浏览、编辑、复习、统计与导出仍完全可用；
          AI 分析与出题会提示未配置，或用本地规则做初步分析。
        </NAlert>
      </NCard>

      <!-- ================= 账号 ================= -->
      <NCard :bordered="false" class="card" title="账号">
        <NDescriptions :column="2" label-placement="left" bordered>
          <NDescriptionsItem label="用户名">{{ auth.user?.username }}</NDescriptionsItem>
          <NDescriptionsItem label="邮箱">{{ auth.user?.email || '未绑定' }}</NDescriptionsItem>
          <NDescriptionsItem label="昵称">{{ auth.user?.display_name || '—' }}</NDescriptionsItem>
          <NDescriptionsItem label="角色">
            <NTag size="small" :type="isAdmin ? 'success' : 'default'" :bordered="false">
              {{ isAdmin ? '管理员' : '成员' }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="AI 权限">
            {{ isAdmin ? '可配置' : '仅查看' }}
          </NDescriptionsItem>
          <NDescriptionsItem label="数据范围">
            仅本人数据{{ isAdmin ? ' + 只读监管' : '' }}
          </NDescriptionsItem>
        </NDescriptions>
      </NCard>

      <!-- ================= 个人资料 ================= -->
      <NCard :bordered="false" class="card" title="个人资料">
        <NForm label-placement="left" label-width="90" :show-feedback="false">
          <NSpace vertical :size="14">
            <NFormItem label="昵称">
              <NInput v-model:value="displayName" placeholder="显示在页面右上角" maxlength="40" />
            </NFormItem>
            <NButton type="primary" :loading="savingProfile" @click="saveProfile">
              保存昵称
            </NButton>
          </NSpace>
        </NForm>
      </NCard>

      <!-- ================= 修改密码 ================= -->
      <NCard :bordered="false" class="card" title="修改密码">
        <NForm label-placement="left" label-width="90" :show-feedback="false">
          <NSpace vertical :size="14">
            <NFormItem label="当前密码">
              <NInput
                v-model:value="currentPassword"
                type="password"
                show-password-on="click"
                placeholder="用于确认身份"
              />
            </NFormItem>
            <NFormItem label="新密码">
              <NInput
                v-model:value="newPassword"
                type="password"
                show-password-on="click"
                placeholder="至少 8 位"
              />
            </NFormItem>
            <NFormItem label="确认新密码">
              <NInput
                v-model:value="confirmPassword"
                type="password"
                show-password-on="click"
                placeholder="再输入一次"
              />
            </NFormItem>
            <NButton type="primary" :loading="savingPassword" @click="changePassword">
              修改密码
            </NButton>
          </NSpace>
        </NForm>
      </NCard>

      <!-- ================= 安装到设备 ================= -->
      <NCard :bordered="false" class="card" title="安装到手机 / 平板">
        <NSpace vertical :size="10">
          <NAlert v-if="isStandalone" type="success" :bordered="false">
            已在独立窗口中运行（已安装）。
          </NAlert>
          <template v-else>
            <div class="config-help">
              本项目是 PWA：用手机浏览器打开后「添加到主屏幕」，即可获得类 App 体验，
              并可直接调用摄像头拍照上传。
            </div>
            <NSpace :size="10" :wrap="true">
              <NButton v-if="canInstall" type="primary" @click="promptInstall">
                一键安装
              </NButton>
              <NButton v-else secondary disabled>请用浏览器菜单「添加到主屏幕」</NButton>
            </NSpace>
            <div class="note">
              iOS 需使用 Safari：分享 → 添加到主屏幕。Android Chrome 会弹出安装提示。
            </div>
          </template>
        </NSpace>
      </NCard>

      <!-- ================= 实例状态 ================= -->
      <NCard :bordered="false" class="card" title="实例状态">
        <NDescriptions v-if="status" :column="2" label-placement="left" bordered>
          <NDescriptionsItem label="版本">{{ status.version }}</NDescriptionsItem>
          <NDescriptionsItem label="存储后端">{{ status.storage_backend }}</NDescriptionsItem>
          <NDescriptionsItem label="OCR 引擎">
            {{ status.ocr.ocr_provider }}（{{ status.ocr.ocr_available ? '可用' : '不可用' }}）
          </NDescriptionsItem>
          <NDescriptionsItem label="文字识别">
            <NTag
              size="small"
              :type="status.ocr.text_extraction ? 'success' : 'warning'"
              :bordered="false"
            >
              {{ status.ocr.text_extraction ? '已启用' : '仅版面切分' }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="AI 提供方">
            <NTag size="small" :type="status.ai_available ? 'success' : 'default'" :bordered="false">
              {{ status.ai_provider }}{{ status.ai_available ? '' : '（未配置）' }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="复习间隔">
            {{ status.review_intervals.join(' / ') }} 天
          </NDescriptionsItem>
          <NDescriptionsItem label="推荐阈值">
            ≥ {{ status.thresholds.recommend }} 推荐 · {{ status.thresholds.suspect }}–{{
              status.thresholds.recommend - 1
            }}
            疑似
          </NDescriptionsItem>
          <NDescriptionsItem label="上传上限">
            {{ status.limits.max_upload_files }} 个 / {{ status.limits.max_file_mb }} MB /
            {{ status.limits.max_pdf_pages }} 页
          </NDescriptionsItem>
        </NDescriptions>

        <NAlert
          v-if="status && !status.ocr.text_extraction"
          type="info"
          :bordered="false"
          style="margin-top: 14px"
        >
          当前使用内置版面引擎：可完成切题区域检测与红笔概率评估，但不提取文字内容。
          安装 PaddleOCR 并设置 <code>OCR_PROVIDER=paddle</code> 可获得文字识别。
        </NAlert>
      </NCard>

      <!-- ================= 环境变量说明 ================= -->
      <NCard :bordered="false" class="card" title="环境变量（可选）">
        <NSpace vertical size="small" class="config-help">
          <div>
            此处配置会覆盖环境变量。也可在 <code>deploy/.env</code> 中预置，适合批量部署：
          </div>
          <div><code>AI_PROVIDER</code> — none / openai / deepseek / glm / gemini / claude / ollama</div>
          <div><code>AI_API_KEY</code> — 云端模型的密钥（Ollama 可留空）</div>
          <div><code>AI_BASE_URL</code> — 自定义网关或 Ollama 地址</div>
          <div><code>AI_MODEL</code> — 模型名称</div>
          <div class="note">任意模型调用失败时，系统自动降级为本地规则分析，不影响错题数据。</div>
        </NSpace>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.card {
  border-radius: 14px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.field-hint {
  font-size: 12px;
  color: var(--mb-muted);
  line-height: 1.8;
}

.actions {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}

.test-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.test-msg {
  margin-top: 4px;
  font-size: 12px;
  word-break: break-all;
  line-height: 1.7;
}

.config-help {
  font-size: 13px;
  color: #334155;
  line-height: 1.9;
}

.config-help code {
  background: #eef2f7;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}

.note {
  color: var(--mb-muted);
  font-size: 12px;
}

@media (max-width: 720px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
