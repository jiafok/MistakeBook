<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NButtonGroup,
  NEmpty,
  NSpin,
  NTag,
  NTooltip,
  NAlert,
  NScrollbar,
  NDivider,
  NBadge,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { Region, Workspace } from '../api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const paperId = computed(() => Number(route.params.id))
const loading = ref(true)
const submitting = ref(false)
const redetecting = ref(false)
const workspace = ref<Workspace | null>(null)
const loadError = ref('')

/** local editing state: regionId -> selected */
const selected = ref<Record<number, boolean>>({})
const originalSelection = ref<Record<number, boolean>>({})
const recorded = ref<Record<number, boolean>>({})

const currentPage = ref(1)
const zoom = ref(1)
/**
 * In fit mode the scale follows the available width; manual zoom disables it.
 * Starting at 1 rendered the image at natural pixel size, which overflowed every
 * phone screen and hid most of the page.
 */
const fitMode = ref(true)
const canvasEl = ref<HTMLElement | null>(null)
const imageEl = ref<HTMLImageElement | null>(null)
const imageLoaded = ref(false)
const imageError = ref(false)
const hoveredId = ref<number | null>(null)
const flashId = ref<number | null>(null)

const undoStack = ref<Array<{ id: number; value: boolean } | { batch: Record<number, boolean> }>>([])

const counts = computed(() => {
  let newCount = 0
  let existing = 0
  for (const region of workspace.value?.regions ?? []) {
    if (recorded.value[region.id]) {
      existing += 1
      continue
    }
    if (selected.value[region.id]) newCount += 1
  }
  return { new: newCount, existing, selected: newCount + existing, total: workspace.value?.regions.length ?? 0 }
})

const pageRegions = computed(() =>
  (workspace.value?.regions ?? []).filter((region) => region.page_no === currentPage.value),
)

const pages = computed(() => workspace.value?.pages ?? [])

const currentPageInfo = computed(() => pages.value.find((p) => p.page_no === currentPage.value) ?? null)

const displayed = computed(() => {
  const info = currentPageInfo.value
  if (!info) return { width: 0, height: 0 }
  return { width: info.width * zoom.value, height: info.height * zoom.value }
})

/** Horizontal padding of `.canvas-viewport`, mirrored from the stylesheet. */
function viewportPadding(): number {
  if (typeof window === 'undefined') return 32
  return window.matchMedia("(max-width: 640px)").matches ? 20 : 40
}

/** Scale that makes the page exactly as wide as the scroll container. */
function fitScaleFor(): number {
  const info = currentPageInfo.value
  const container = canvasEl.value
  if (!info || !container) return 1
  const available = container.clientWidth - viewportPadding()
  if (available <= 0 || info.width <= 0) return 1
  // Never upscale beyond 1: a small scan would otherwise blur.
  return Math.min(1, available / info.width)
}

/** Apply fit only while the user has not taken manual control of the zoom. */
function applyFit(): void {
  if (!fitMode.value) return
  zoom.value = Number(fitScaleFor().toFixed(4))
}

function fitToWidth(): void {
  fitMode.value = true
  applyFit()
}

function setZoom(next: number): void {
  // Any explicit zoom is a manual decision: stop overriding it on resize.
  fitMode.value = false
  zoom.value = Math.min(4, Math.max(0.25, Number(next.toFixed(2))))
}

const basketSelected = computed(() =>
  (workspace.value?.regions ?? []).filter((region) => selected.value[region.id] && !recorded.value[region.id]),
)

function bandClass(region: Region): string {
  if (recorded.value[region.id]) return 'is-recorded'
  if (selected.value[region.id]) return 'is-pending'
  if (region.band === 'recommended') return 'is-recommend'
  if (region.band === 'suspected') return 'is-suspect'
  return 'is-normal'
}

function bandLabel(band: string): string {
  if (band === 'recommended') return 'AI 推荐'
  if (band === 'suspected') return '疑似'
  if (band === 'unknown') return '未评估'
  return '普通'
}

function bandTagType(band: string): 'success' | 'warning' | 'default' {
  if (band === 'recommended') return 'success'
  if (band === 'suspected') return 'warning'
  return 'default'
}

function regionStyle(region: Region) {
  const scale = zoom.value
  return {
    left: `${region.x * scale}px`,
    top: `${region.y * scale}px`,
    width: `${region.width * scale}px`,
    height: `${region.height * scale}px`,
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await api.workspace(paperId.value)
    workspace.value = data
    const draft: Record<number, boolean> = {}
    const rec: Record<number, boolean> = {}
    for (const region of data.regions) {
      draft[region.id] = region.is_selected
      rec[region.id] = region.is_recorded
    }
    selected.value = draft
    originalSelection.value = { ...draft }
    recorded.value = rec
    currentPage.value = data.pages[0]?.page_no ?? 1
  } catch (error) {
    loadError.value = apiErrorMessage(error)
  } finally {
    loading.value = false
  }
}

function toggle(region: Region) {
  if (recorded.value[region.id]) {
    message.info('该题已在错题本中')
    return
  }
  const next = !selected.value[region.id]
  selected.value = { ...selected.value, [region.id]: next }
  undoStack.value.push({ id: region.id, value: !next })
  if (undoStack.value.length > 50) undoStack.value.shift()
  persistSoon()
}

function applyBatch(predicate: (region: Region) => boolean) {
  const before: Record<number, boolean> = {}
  const next = { ...selected.value }
  for (const region of pageRegions.value) {
    if (recorded.value[region.id]) continue
    before[region.id] = selected.value[region.id] ?? false
    next[region.id] = predicate(region)
  }
  selected.value = next
  undoStack.value.push({ batch: before })
  if (undoStack.value.length > 50) undoStack.value.shift()
  persistSoon()
}

function selectAll() {
  applyBatch(() => true)
}
function selectNone() {
  applyBatch(() => false)
}
function selectRecommended() {
  applyBatch((region) => region.band === 'recommended')
}
function selectByBand(band: string) {
  applyBatch((region) => region.band === band)
}

function undo() {
  const last = undoStack.value.pop()
  if (!last) return
  if ('id' in last) {
    selected.value = { ...selected.value, [last.id]: last.value }
  } else {
    selected.value = { ...selected.value, ...last.batch }
  }
  persistSoon()
}

let saveTimer: number | undefined
function persistSoon() {
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    const diff: Record<number, boolean> = {}
    const all = new Set([
      ...Object.keys(selected.value).map(Number),
      ...Object.keys(originalSelection.value).map(Number),
    ])
    for (const id of all) {
      if ((selected.value[id] ?? false) !== (originalSelection.value[id] ?? false)) {
        diff[id] = selected.value[id] ?? false
      }
    }
    if (Object.keys(diff).length === 0) return
    api
      .saveSelections(paperId.value, diff)
      .then(() => {
        originalSelection.value = { ...selected.value }
      })
      .catch(() => undefined)
  }, 500)
}

function focusRegion(region: Region) {
  if (region.page_no !== currentPage.value) {
    currentPage.value = region.page_no
    nextTick(() => scrollToRegion(region))
    return
  }
  scrollToRegion(region)
}

function scrollToRegion(region: Region) {
  const el = canvasEl.value?.querySelector<HTMLElement>(`[data-region="${region.id}"]`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
  flashId.value = region.id
  window.setTimeout(() => {
    if (flashId.value === region.id) flashId.value = null
  }, 1200)
}

async function redetect() {
  redetecting.value = true
  try {
    await api.reanalyzePaper(paperId.value)
    // Region ids are regenerated, so drop the undo history and reload.
    undoStack.value = []
    await load()
    message.success('已重新识别题目区域')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    redetecting.value = false
  }
}

async function submit() {
  const ids = basketSelected.value.map((region) => region.id)
  if (ids.length === 0) {
    message.warning('请先选择要加入错题本的题目')
    return
  }
  submitting.value = true
  try {
    const result = await api.commit(paperId.value, ids)
    if (result.failed > 0) {
      message.warning(`新增 ${result.created} 题，已存在 ${result.existing} 题，失败 ${result.failed} 题`)
    } else {
      message.success(`已加入错题本：新增 ${result.created} 题，已存在 ${result.existing} 题`)
    }
    if (result.created > 0) {
      router.push({ name: 'mistakes' })
      return
    }
    await load()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    submitting.value = false
  }
}

function changePage(delta: number) {
  const next = currentPage.value + delta
  if (pages.value.some((p) => p.page_no === next)) {
    currentPage.value = next
    imageLoaded.value = false
    imageError.value = false
  }
}

function onImageLoad() {
  imageLoaded.value = true
  imageError.value = false
  // The container has its final size only once content is rendered.
  nextTick(() => applyFit())
}

function onResize(): void {
  applyFit()
}

function onImageError() {
  imageLoaded.value = false
  imageError.value = true
}

function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault()
    undo()
  }
  if ((event.ctrlKey || event.metaKey) && (event.key === '+' || event.key === '=')) {
    event.preventDefault()
    setZoom(zoom.value + 0.25)
  }
  if ((event.ctrlKey || event.metaKey) && event.key === '-') {
    event.preventDefault()
    setZoom(zoom.value - 0.25)
  }
}

watch(currentPage, () => {
  imageLoaded.value = false
  imageError.value = false
  nextTick(() => {
    canvasEl.value?.querySelector<HTMLElement>('.canvas-viewport')?.scrollTo({ top: 0, left: 0 })
    applyFit()
  })
})

onMounted(() => {
  load()
  window.addEventListener('keydown', onKeydown)
  window.addEventListener('resize', onResize)
  window.addEventListener('orientationchange', onResize)
})

onBeforeUnmount(() => {
  window.clearTimeout(saveTimer)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('orientationchange', onResize)
})
</script>

<template>
  <div class="workspace">
    <NSpin v-if="loading" size="large" class="workspace-loader" description="正在载入题目区域…" />

    <template v-else-if="loadError">
      <div class="mb-page">
        <NAlert type="error" title="载入失败">{{ loadError }}</NAlert>
        <NButton style="margin-top: 16px" @click="load">重试</NButton>
      </div>
    </template>

    <template v-else-if="workspace">
      <!-- top toolbar -->
      <header class="ws-toolbar">
        <div class="ws-title">
          <NButton size="small" quaternary @click="router.push({ name: 'dashboard' })">← 返回</NButton>
          <div>
            <div class="ws-paper-title">{{ workspace.paper.title }}</div>
            <div class="ws-paper-sub">
              <NTag size="small" :bordered="false">{{ workspace.paper.subject }}</NTag>
              <NTag
                v-if="workspace.paper.subject_source === 'auto'"
                size="small"
                :bordered="false"
                type="info"
              >
                自动识别
              </NTag>
              <span>共 {{ workspace.paper.page_count }} 页 · {{ counts.total }} 个题目区域</span>
            </div>
          </div>
        </div>

        <div class="ws-page-nav">
          <NButtonGroup size="small">
            <NButton :disabled="currentPage <= 1" @click="changePage(-1)">上一页</NButton>
            <NButton disabled>{{ currentPage }} / {{ pages.length }}</NButton>
            <NButton :disabled="currentPage >= pages.length" @click="changePage(1)">下一页</NButton>
          </NButtonGroup>
        </div>

        <div class="ws-zoom">
          <NButton size="small" @click="setZoom(zoom - 0.25)">−</NButton>
          <NButton size="small" :type="fitMode ? 'primary' : 'default'" @click="fitToWidth">
            适应宽度
          </NButton>
          <NButton size="small" @click="setZoom(zoom + 0.25)">＋</NButton>
          <span class="ws-zoom-value">{{ Math.round(zoom * 100) }}%</span>
        </div>
      </header>

      <div class="ws-body">
        <!-- canvas -->
        <section class="ws-canvas">
          <div class="ws-batch">
            <NButton size="small" @click="selectAll">全选</NButton>
            <NButton size="small" @click="selectNone">全不选</NButton>
            <NButton size="small" type="primary" secondary @click="selectRecommended">仅选 AI 推荐</NButton>
            <NDivider vertical />
            <NButton size="small" quaternary @click="selectByBand('suspected')">仅选疑似</NButton>
            <NButton size="small" quaternary @click="selectByBand('normal')">仅选普通</NButton>
            <NDivider vertical />
            <NTooltip>
              <template #trigger>
                <NButton size="small" quaternary @click="undo">撤销 (Ctrl+Z)</NButton>
              </template>
              撤销上一次切换或批量操作
            </NTooltip>
            <NTooltip>
              <template #trigger>
                <NButton size="small" quaternary :loading="redetecting" @click="redetect">
                  重新识别
                </NButton>
              </template>
              区域位置不准时，用当前引擎重新切题（会清空已选）
            </NTooltip>
          </div>

          <div ref="canvasEl" class="canvas-scroll">
            <div class="canvas-viewport">
              <div v-if="imageError" class="canvas-error">
                <NAlert type="warning" :bordered="false">
                  原图加载失败，请检查存储配置后重试。
                </NAlert>
                <NButton size="small" style="margin-top: 10px" @click="imageError = false">
                  重试
                </NButton>
              </div>

              <NEmpty v-if="!pageRegions.length && !imageError" description="本页未识别到题目区域" style="padding: 40px" />

              <div
                v-show="!imageError"
                class="canvas-stage"
                :style="{ width: `${displayed.width}px`, height: `${displayed.height}px` }"
              >
                <img
                  ref="imageEl"
                  :src="api.pageImageUrl(workspace.paper.id, currentPage)"
                  class="canvas-image"
                  :style="{ width: `${displayed.width}px`, height: `${displayed.height}px` }"
                  alt="试卷页面"
                  @load="onImageLoad"
                  @error="onImageError"
                />

                <div
                  v-for="region in pageRegions"
                  :key="region.id"
                  :data-region="region.id"
                  class="region-box"
                  :class="[bandClass(region), { 'is-flash': flashId === region.id }]"
                  :style="regionStyle(region)"
                  role="button"
                  tabindex="0"
                  :aria-label="`${region.label}，${bandLabel(region.band)}，概率 ${region.wrong_probability ?? '未评估'}，${recorded[region.id] ? '已收录' : selected[region.id] ? '待收录' : '未收录'}`"
                  @click="toggle(region)"
                  @keydown.space.prevent="toggle(region)"
                  @keydown.enter.prevent="toggle(region)"
                  @mouseenter="hoveredId = region.id"
                  @mouseleave="hoveredId = null"
                  @focus="hoveredId = region.id"
                  @blur="hoveredId = null"
                >
                  <span class="region-index">{{ region.label }}</span>

                  <span class="region-badge" :class="`badge-${region.band}`">
                    {{ region.wrong_probability ?? '—' }}
                  </span>

                  <span v-if="recorded[region.id]" class="region-recorded-tag">已收录</span>

                  <span class="region-button" :class="{ 'is-on': selected[region.id] && !recorded[region.id] }">
                    {{ recorded[region.id] ? '·' : selected[region.id] ? '✓' : '+' }}
                  </span>

                  <Transition name="tip">
                    <div v-if="hoveredId === region.id" class="region-tip" @click.stop>
                      <div class="tip-head">
                        <strong>{{ region.label }}</strong>
                        <NTag size="tiny" :type="bandTagType(region.band)" :bordered="false">
                          {{ bandLabel(region.band) }}
                          <template v-if="region.wrong_probability !== null"> · {{ region.wrong_probability }}</template>
                        </NTag>
                      </div>
                      <div class="tip-text">{{ region.text || '（未识别到文字）' }}</div>
                      <div v-if="region.detection_status !== 'ok'" class="tip-warn">
                        检测状态：{{ region.detection_status }}
                      </div>
                    </div>
                  </Transition>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- question basket -->
        <aside class="ws-basket">
          <div class="basket-head">
            <strong>错题篮</strong>
            <NBadge :value="basketSelected.length" type="success" />
          </div>
          <p class="basket-hint">点击题目可定位到原图位置</p>
          <NDivider style="margin: 8px 0" />

          <NScrollbar style="max-height: calc(100vh - 320px)">
            <div v-if="!basketSelected.length" class="basket-empty">
              还没有选择题目<br /><span>在左侧图片上点击 + 即可加入</span>
            </div>
            <button
              v-for="region in basketSelected"
              :key="region.id"
              class="basket-item"
              @click="focusRegion(region)"
            >
              <div class="basket-item-head">
                <span class="basket-label">{{ region.label }}</span>
                <NTag size="tiny" :type="bandTagType(region.band)" :bordered="false">
                  {{ region.wrong_probability ?? '—' }}
                </NTag>
              </div>
              <div class="basket-text">{{ region.text || '（未识别到文字）' }}</div>
              <div class="basket-page">第 {{ region.page_no }} 页</div>
            </button>
          </NScrollbar>
        </aside>
      </div>

      <!-- footer counters -->
      <footer class="ws-footer">
        <div class="ws-counts">
          <div class="count">
            <span>已选择</span>
            <strong>{{ counts.selected }}</strong>
          </div>
          <div class="count count-new">
            <span>预计新增</span>
            <strong>{{ counts.new }}</strong>
          </div>
          <div class="count count-existing">
            <span>已存在</span>
            <strong>{{ counts.existing }}</strong>
          </div>
        </div>
        <div class="ws-footer-actions">
          <NButton @click="router.push({ name: 'dashboard' })">稍后处理</NButton>
          <NButton type="primary" size="large" :loading="submitting" :disabled="!counts.new" @click="submit">
            加入错题本
          </NButton>
        </div>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.workspace {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 64px);
}

.workspace-loader {
  display: grid;
  place-items: center;
  padding: 120px 0;
}

.ws-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 20px;
  background: var(--mb-panel);
  border-bottom: 1px solid var(--mb-border);
  position: sticky;
  top: 0;
  z-index: 20;
}

.ws-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ws-paper-title {
  font-weight: 650;
  font-size: 15px;
}

.ws-paper-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--mb-muted);
  margin-top: 2px;
}

.ws-page-nav,
.ws-zoom {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ws-zoom-value {
  font-size: 12px;
  color: var(--mb-muted);
  min-width: 42px;
  text-align: right;
}

.ws-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 0;
  flex: 1;
  min-height: 0;
}

.ws-canvas {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #eef1f6;
}

.ws-batch {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 10px 16px;
  background: var(--mb-panel);
  border-bottom: 1px solid var(--mb-border);
}

.canvas-scroll {
  flex: 1;
  /* Only scroll when the user actually zoomed past the container width. */
  overflow: auto;
  -webkit-overflow-scrolling: touch;
  min-width: 0;
}

.canvas-viewport {
  /* Flex (not grid) centring: the viewport stays at container width, so an image
     that fits does not create a horizontal scrollbar. */
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 20px;
  min-height: 100%;
}

.canvas-error {
  padding: 16px;
  width: min(520px, 90%);
}

.canvas-stage {
  /* flex: never shrink below the scaled page size, so zoom stays predictable */
  flex: 0 0 auto;
  position: relative;
  box-shadow: 0 2px 18px rgba(15, 23, 42, 0.12);
  background: #fff;
}

.canvas-image {
  display: block;
  user-select: none;
  -webkit-user-drag: none;
}

/* ---- region states: single-channel border priority ---- */
.region-box {
  position: absolute;
  border-radius: 4px;
  cursor: pointer;
  transition: box-shadow 0.12s ease, border-color 0.12s ease;
  outline: none;
}

/* priority 4: normal not selected */
.region-box.is-normal {
  border: 2px solid var(--mb-normal);
  background: rgba(154, 163, 175, 0.06);
}

/* priority 3b: suspected not selected */
.region-box.is-suspect {
  border: 2px solid var(--mb-suspect);
  background: rgba(240, 180, 41, 0.09);
}

/* priority 3a: recommended but user cancelled */
.region-box.is-recommend {
  border: 2px solid var(--mb-recommend);
  background: rgba(31, 157, 85, 0.05);
  border-style: dashed;
}

/* priority 2: pending (selected this session) */
.region-box.is-pending {
  border: 2px solid var(--mb-recommend);
  background: rgba(31, 157, 85, 0.10);
  box-shadow: 0 0 0 2px rgba(31, 157, 85, 0.18);
}

/* priority 1: already recorded */
.region-box.is-recorded {
  border: 2px solid #7fd3a5;
  background: var(--mb-recorded);
  cursor: default;
}

.region-box.is-flash {
  animation: flash 1.2s ease;
}

@keyframes flash {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(37, 99, 235, 0);
  }
  40% {
    box-shadow: 0 0 0 6px rgba(37, 99, 235, 0.4);
  }
}

.region-index {
  position: absolute;
  top: -22px;
  left: -2px;
  font-size: 11px;
  font-weight: 600;
  color: #334155;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid var(--mb-border);
  border-radius: 4px;
  padding: 0 5px;
  white-space: nowrap;
}

.region-badge {
  position: absolute;
  top: -10px;
  right: -8px;
  min-width: 22px;
  height: 20px;
  padding: 0 5px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
}

.badge-recommended {
  background: var(--mb-recommend);
}
.badge-suspected {
  background: var(--mb-suspect);
  color: #4a3200;
}
.badge-normal,
.badge-unknown {
  background: var(--mb-normal);
}

.region-recorded-tag {
  position: absolute;
  bottom: -10px;
  right: 4px;
  background: #0f7a46;
  color: #fff;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
}

/* center + / check button */
.region-button {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  border: 2px solid var(--mb-normal);
  color: #374151;
  font-size: 16px;
  font-weight: 700;
  line-height: 1;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.18);
  pointer-events: none;
}

.region-button.is-on {
  background: var(--mb-recommend);
  border-color: var(--mb-recommend);
  color: #fff;
}

.region-box:hover .region-button,
.region-box:focus .region-button {
  transform: translate(-50%, -50%) scale(1.12);
}

.region-tip {
  position: absolute;
  left: 0;
  top: calc(100% + 8px);
  width: min(340px, 70vw);
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: 8px;
  z-index: 40;
  pointer-events: none;
  box-shadow: 0 6px 24px rgba(2, 6, 23, 0.35);
}

.tip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
}

.tip-text {
  max-height: 5.2em;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
}

.tip-warn {
  margin-top: 4px;
  color: #fbbf24;
}

.tip-enter-active,
.tip-leave-active {
  transition: opacity 0.12s ease;
}
.tip-enter-from,
.tip-leave-to {
  opacity: 0;
}

/* ---- basket ---- */
.ws-basket {
  border-left: 1px solid var(--mb-border);
  background: var(--mb-panel);
  padding: 14px 14px 20px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.basket-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
}

.basket-hint {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--mb-muted);
}

.basket-empty {
  padding: 28px 8px;
  text-align: center;
  color: var(--mb-muted);
  font-size: 12px;
  line-height: 1.8;
}

.basket-empty span {
  font-size: 11px;
  color: #9aa3af;
}

.basket-item {
  display: block;
  width: 100%;
  text-align: left;
  border: 1px solid var(--mb-border);
  background: #fbfcfe;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}

.basket-item:hover {
  border-color: var(--mb-recommend);
  background: #f4fbf7;
}

.basket-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 4px;
}

.basket-label {
  font-size: 12px;
  font-weight: 600;
}

.basket-text {
  font-size: 11px;
  color: #4b5563;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
}

.basket-page {
  margin-top: 4px;
  font-size: 10px;
  color: #9aa3af;
}

/* ---- footer ---- */
.ws-footer {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 20px;
  background: var(--mb-panel);
  border-top: 1px solid var(--mb-border);
  box-shadow: 0 -4px 18px rgba(15, 23, 42, 0.06);
  z-index: 20;
}

.ws-counts {
  display: flex;
  align-items: center;
  gap: 22px;
}

.count {
  display: flex;
  flex-direction: column;
  font-size: 11px;
  color: var(--mb-muted);
}

.count strong {
  font-size: 20px;
  color: var(--mb-text);
  line-height: 1.2;
}

.count-new strong {
  color: var(--mb-recommend);
}

.count-existing strong {
  color: #0f7a46;
}

.ws-footer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

@media (max-width: 1080px) {
  .ws-body {
    grid-template-columns: 1fr;
  }
  .ws-basket {
    border-left: none;
    border-top: 1px solid var(--mb-border);
    max-height: 320px;
  }
}

/* ---- phones / small tablets: keep everything reachable with one thumb ---- */
@media (max-width: 640px) {
  .workspace {
    min-height: calc(100dvh - 58px);
  }

  .ws-toolbar {
    padding: 8px 12px;
    padding-left: max(12px, env(safe-area-inset-left));
    padding-right: max(12px, env(safe-area-inset-right));
    gap: 8px;
  }

  .ws-paper-title {
    font-size: 14px;
  }

  .ws-page-nav :deep(.n-button),
  .ws-zoom :deep(.n-button) {
    min-width: 40px;
    min-height: 40px;
  }

  .ws-zoom-value {
    min-width: 38px;
  }

  .ws-batch {
    padding: 8px 12px;
    overflow-x: auto;
    flex-wrap: nowrap;
    -webkit-overflow-scrolling: touch;
  }

  .ws-batch :deep(.n-button) {
    min-height: 36px;
    flex: 0 0 auto;
  }

  .canvas-viewport {
    padding: 10px;
  }

  .ws-basket {
    max-height: 46dvh;
    padding: 12px;
  }

  .ws-footer {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    padding: 10px 12px;
    padding-bottom: max(10px, env(safe-area-inset-bottom));
  }

  .ws-counts {
    justify-content: space-between;
    gap: 8px;
  }

  .count strong {
    font-size: 18px;
  }

  .ws-footer-actions {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 10px;
  }

  .ws-footer-actions :deep(.n-button) {
    min-height: 44px;
  }

  .region-tip {
    width: min(280px, 78vw);
  }
}
</style>
