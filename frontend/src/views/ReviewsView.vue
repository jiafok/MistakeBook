<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NButton,
  NCard,
  NEmpty,
  NSpin,
  NTabPane,
  NTabs,
  NTag,
  NSpace,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { ReviewItem } from '../api/types'

const message = useMessage()

const horizon = ref<'today' | 'week' | 'month'>('today')
const items = ref<ReviewItem[]>([])
const loading = ref(false)
const busy = ref<number | null>(null)

const horizonLabel = computed(
  () => ({ today: '今日复习', week: '本周复习', month: '本月复习' })[horizon.value],
)

async function load() {
  loading.value = true
  try {
    items.value = await api.reviews(horizon.value)
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function submit(planId: number, result: 'pass' | 'fail' | 'skip') {
  busy.value = planId
  try {
    await api.submitReview(planId, result)
    items.value = items.value.filter((item) => item.plan_id !== planId)
    if (result === 'pass') message.success('已记录：掌握')
    else if (result === 'fail') message.info('已记录：未掌握，稍后重新安排')
    else message.info('已跳过，明日再复习')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    busy.value = null
  }
}

function onTab(value: string) {
  horizon.value = value as 'today' | 'week' | 'month'
  load()
}

onMounted(load)
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">复习计划</h2>
        <p class="mb-page-subtitle">基于艾宾浩斯遗忘曲线：1 / 3 / 7 / 15 / 30 天</p>
      </div>
      <NButton @click="load">刷新</NButton>
    </div>

    <NTabs :value="horizon" type="segment" @update:value="onTab">
      <NTabPane name="today" tab="今日" />
      <NTabPane name="week" tab="本周" />
      <NTabPane name="month" tab="本月" />
    </NTabs>

    <NSpin :show="loading">
      <NEmpty
        v-if="!loading && !items.length"
        :description="`${horizonLabel}没有到期内容，保持节奏`"
        class="mb-empty"
      />

      <div v-else class="review-list">
        <NCard v-for="item in items" :key="item.plan_id" :bordered="false" class="review-card">
          <div class="review-head">
            <div class="review-title">
              <strong>{{ item.label || '题目' }}</strong>
              <NTag size="small" :bordered="false">{{ item.subject }}</NTag>
              <NTag size="small" :bordered="false" type="info">第 {{ item.stage + 1 }} 阶段</NTag>
            </div>
            <span class="review-due">到期：{{ new Date(item.due_at).toLocaleDateString() }}</span>
          </div>

          <div class="review-text">{{ item.text || '（无识别文本）' }}</div>

          <div v-if="item.knowledge_points.length" class="review-kps">
            <NTag v-for="kp in item.knowledge_points" :key="kp" size="small" :bordered="false" type="success">
              {{ kp }}
            </NTag>
          </div>

          <NSpace class="review-actions">
            <NButton size="small" type="primary" :loading="busy === item.plan_id" @click="submit(item.plan_id, 'pass')">
              已掌握
            </NButton>
            <NButton size="small" type="warning" secondary :loading="busy === item.plan_id" @click="submit(item.plan_id, 'fail')">
              未掌握
            </NButton>
            <NButton size="small" quaternary :loading="busy === item.plan_id" @click="submit(item.plan_id, 'skip')">
              跳过
            </NButton>
          </NSpace>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.review-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
}

.review-card {
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.review-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.review-due {
  font-size: 11px;
  color: var(--mb-muted);
}

.review-text {
  font-size: 13px;
  line-height: 1.8;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 10px;
}

.review-kps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.review-actions {
  border-top: 1px dashed var(--mb-border);
  padding-top: 10px;
}
</style>
