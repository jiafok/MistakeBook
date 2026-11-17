<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NInput,
  NProgress,
  NSelect,
  NSpace,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'

const router = useRouter()
const route = useRoute()
const message = useMessage()

const subject = ref('数学')
const title = ref('')
const files = ref<File[]>([])
const uploading = ref(false)
const progress = ref(0)
const cameraInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)

const subjectOptions = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理', '政治'].map(
  (value) => ({ label: value, value }),
)

const isTouchDevice = ref(false)

const totalSize = computed(() => files.value.reduce((sum, file) => sum + file.size, 0))

const canAddMore = computed(() => files.value.length < 500)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const ALLOWED = ['jpg', 'jpeg', 'png', 'webp', 'pdf']

function addFiles(incoming: FileList | null) {
  if (!incoming || incoming.length === 0) return
  const accepted: File[] = []
  const rejected: string[] = []

  for (const file of Array.from(incoming)) {
    const ext = (file.name.split('.').pop() || '').toLowerCase()
    if (!ALLOWED.includes(ext)) {
      rejected.push(`${file.name}（不支持的类型）`)
      continue
    }
    if (file.size > 30 * 1024 * 1024) {
      rejected.push(`${file.name}（超过 30MB）`)
      continue
    }
    accepted.push(file)
  }

  const room = 500 - files.value.length
  if (accepted.length > room) {
    rejected.push(`超出单次 500 个文件上限，多余 ${accepted.length - room} 个已忽略`)
  }

  files.value = [...files.value, ...accepted.slice(0, Math.max(0, room))]

  if (rejected.length) {
    message.warning(rejected.slice(0, 3).join('；') + (rejected.length > 3 ? ' …' : ''))
  }
  if (accepted.length) {
    message.success(`已添加 ${Math.min(accepted.length, Math.max(0, room))} 个文件`)
  }
}

function onCameraChange(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files)
  input.value = '' // allow re-capturing the same filename
}

function onGalleryChange(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files)
  input.value = ''
}

function openCamera() {
  if (!canAddMore.value) {
    message.warning('已达到 500 个文件上限')
    return
  }
  cameraInput.value?.click()
}

function openGallery() {
  if (!canAddMore.value) {
    message.warning('已达到 500 个文件上限')
    return
  }
  galleryInput.value?.click()
}

function removeFile(index: number) {
  files.value = files.value.filter((_, i) => i !== index)
}

function clearAll() {
  files.value = []
}

async function upload() {
  if (!files.value.length) {
    message.warning('请先拍照或选择文件')
    return
  }
  if (!canAddMore.value) {
    message.error('单次最多上传 500 个文件')
    return
  }

  uploading.value = true
  progress.value = 20
  try {
    const paper = await api.uploadPapers(files.value, title.value.trim(), subject.value)
    progress.value = 100
    if (paper.status === 'failed') {
      message.error(paper.error_message || '处理失败')
      return
    }
    if (paper.error_message) {
      message.warning(`部分文件未处理：${paper.error_message}`)
    }
    message.success(`已识别 ${paper.page_count} 页，正在进入确认工作台`)
    router.push({ name: 'workspace', params: { id: paper.id } })
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    uploading.value = false
    progress.value = 0
  }
}

onMounted(() => {
  isTouchDevice.value =
    'ontouchstart' in window || (navigator.maxTouchPoints ?? 0) > 0
  // PWA shortcut / deep link: /upload?camera=1 opens the camera immediately.
  if (route.query.camera === '1') {
    setTimeout(openCamera, 350)
  }
})
</script>

<template>
  <div class="mb-page upload-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">上传试卷</h2>
        <p class="mb-page-subtitle">
          支持拍照、相册与 PDF 文件，单次 1–500 个。上传后自动切题并进入确认工作台。
        </p>
      </div>
    </div>

    <!-- hidden inputs: one triggers the rear camera, one opens the file picker -->
    <input
      ref="cameraInput"
      class="hidden-input"
      type="file"
      accept="image/*"
      capture="environment"
      @change="onCameraChange"
    />
    <input
      ref="galleryInput"
      class="hidden-input"
      type="file"
      accept=".jpg,.jpeg,.png,.webp,.pdf,image/*,application/pdf"
      multiple
      @change="onGalleryChange"
    />

    <NCard :bordered="false" class="card">
      <NSpace vertical size="large">
        <div class="capture-grid">
          <button class="capture-btn capture-camera" type="button" @click="openCamera">
            <span class="capture-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M4 8h3l1.6-2h6.8L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z" />
                <circle cx="12" cy="13" r="3.4" />
              </svg>
            </span>
            <span class="capture-title">拍照上传</span>
            <span class="capture-hint">调用设备摄像头</span>
          </button>

          <button class="capture-btn capture-gallery" type="button" @click="openGallery">
            <span class="capture-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="m4 16 4.5-4.5 3 3L15 11l5 5" />
                <circle cx="9" cy="9" r="1.4" />
              </svg>
            </span>
            <span class="capture-title">从相册或文件选择</span>
            <span class="capture-hint">图片 / PDF，可多选</span>
          </button>
        </div>

        <NAlert v-if="isTouchDevice" type="info" :bordered="false">
          在手机上可直接调用摄像头拍照；连续拍摄会自动追加到下方列表。
        </NAlert>

        <div class="field-row">
          <div class="field">
            <label class="field-label">试卷名称</label>
            <NInput v-model:value="title" placeholder="例如：数学 Unit 3 单元测（可留空）" />
          </div>
          <div class="field field-narrow">
            <label class="field-label">学科</label>
            <NSelect v-model:value="subject" :options="subjectOptions" />
          </div>
        </div>

        <div v-if="files.length" class="file-block">
          <div class="file-head">
            <span>
              已选择 <strong>{{ files.length }}</strong> 个文件 · {{ formatSize(totalSize) }}
            </span>
            <NButton size="tiny" quaternary @click="clearAll">全部清空</NButton>
          </div>

          <div class="file-grid">
            <div v-for="(file, index) in files" :key="`${file.name}-${index}`" class="file-chip">
              <span class="file-ext">{{ (file.name.split('.').pop() || '').toUpperCase() }}</span>
              <span class="file-name" :title="file.name">{{ file.name }}</span>
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <button
                class="file-remove"
                type="button"
                :aria-label="`移除 ${file.name}`"
                @click="removeFile(index)"
              >
                ×
              </button>
            </div>
          </div>
        </div>

        <NProgress v-if="uploading" :percentage="progress" :show-indicator="false" processing />

        <div class="actions">
          <NButton @click="router.push({ name: 'dashboard' })">取消</NButton>
          <NButton
            type="primary"
            size="large"
            :loading="uploading"
            :disabled="!files.length"
            block
            @click="upload"
          >
            开始识别（{{ files.length || 0 }}）
          </NButton>
        </div>
      </NSpace>
    </NCard>

    <NCard :bordered="false" class="card hint-card" title="处理流程">
      <ol class="flow">
        <li>拍照或选择文件，系统校验类型、大小与页数</li>
        <li>OCR 识别文字，检测题目区域坐标（Question Region）</li>
        <li>分析红笔批改痕迹，计算错题概率（0–100）</li>
        <li>自动进入确认工作台，概率 ≥ 85 的题目默认预选</li>
        <li>你确认后点击「加入错题本」才真正入库</li>
      </ol>
    </NCard>
  </div>
</template>

<style scoped>
.hidden-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.card {
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.hint-card {
  margin-top: 16px;
}

/* ---- capture entry points: large touch targets ---- */
.capture-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.capture-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 132px;
  padding: 18px 12px;
  border-radius: 14px;
  border: 1.5px dashed var(--mb-border);
  background: #fbfcfe;
  color: #334155;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease, transform 0.1s ease;
  -webkit-tap-highlight-color: transparent;
}

.capture-btn:hover {
  border-color: var(--mb-brand);
  background: var(--mb-brand-soft);
}

.capture-btn:active {
  transform: scale(0.985);
}

.capture-camera {
  border-color: #bcd0f7;
  background: linear-gradient(160deg, #f2f7ff, #eaf1ff);
  color: #1d4ed8;
}

.capture-camera .capture-icon {
  background: #fff;
  color: #2563eb;
}

.capture-gallery .capture-icon {
  background: #fff;
  color: #475569;
}

.capture-icon {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
}

.capture-title {
  font-size: 14px;
  font-weight: 600;
}

.capture-hint {
  font-size: 12px;
  color: var(--mb-muted);
}

/* ---- fields ---- */
.field-row {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}

.field {
  flex: 1;
  min-width: 200px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-narrow {
  flex: 0 0 160px;
  min-width: 140px;
}

.field-label {
  font-size: 13px;
  color: #475569;
}

/* ---- selected files ---- */
.file-block {
  border: 1px solid var(--mb-border);
  border-radius: 12px;
  padding: 12px;
  background: #fbfcfe;
}

.file-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: #475569;
  margin-bottom: 10px;
}

.file-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}

.file-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border: 1px solid var(--mb-border);
  border-radius: 9px;
  background: #fff;
  font-size: 12px;
  min-width: 0;
}

.file-ext {
  flex: 0 0 auto;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--mb-brand-soft);
  color: var(--mb-brand);
  font-size: 10px;
  font-weight: 700;
}

.file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #334155;
}

.file-size {
  flex: 0 0 auto;
  color: #94a3b8;
  font-size: 11px;
}

.file-remove {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: #f1f5f9;
  color: #64748b;
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
}

.file-remove:hover {
  background: #fee2e2;
  color: #b91c1c;
}

.actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.actions > :last-child {
  flex: 1;
  min-width: 180px;
}

.flow {
  margin: 0;
  padding-left: 20px;
  line-height: 2;
  font-size: 13px;
  color: #475569;
}

/* ---- phone ---- */
@media (max-width: 640px) {
  .mb-page.upload-page {
    padding: 14px 12px 40px;
  }

  .capture-grid {
    grid-template-columns: 1fr;
  }

  .capture-btn {
    min-height: 104px;
    flex-direction: row;
    justify-content: flex-start;
    gap: 12px;
    padding: 16px;
    text-align: left;
  }

  .capture-btn .capture-title,
  .capture-btn .capture-hint {
    display: block;
  }

  .capture-btn > span:nth-child(2) {
    flex: 1;
  }

  .field-narrow {
    flex: 1 1 100%;
  }

  .file-grid {
    grid-template-columns: 1fr;
    max-height: 200px;
  }
}
</style>
