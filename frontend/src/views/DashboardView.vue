<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NEmpty,
  NGi,
  NGrid,
  NStatistic,
  NTag,
  NSpace,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { Analytics, Paper, ReviewItem } from '../api/types'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const message = useMessage()
const auth = useAuthStore()

const papers = ref<Paper[]>([])
const analytics = ref<Analytics | null>(null)
const reviews = ref<ReviewItem[]>([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [paperList, stats, todays] = await Promise.all([
      api.listPapers(),
      api.analytics(),
      api.reviews('today'),
    ])
    papers.value = paperList
    analytics.value = stats
    reviews.value = todays
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function statusType(status: string): 'success' | 'warning' | 'error' | 'default' {
  if (status === 'ready') return 'success'
  if (status === 'processing') return 'warning'
  if (status === 'failed') return 'error'
  return 'default'
}

function openPaper(paper: Paper) {
  if (paper.status === 'ready') {
    router.push({ name: 'workspace', params: { id: paper.id } })
  } else {
    message.info('该试卷仍在处理中或处理失败')
  }
}

onMounted(() => {
  load()
  auth.loadStatus().catch(() => undefined)
})
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">你好，{{ auth.user?.display_name || '同学' }}</h2>
        <p class="mb-page-subtitle">AI 已完成预处理，最终收录由你决定。</p>
      </div>
      <NSpace>
        <NButton @click="router.push({ name: 'upload' })">上传试卷</NButton>
        <NButton type="primary" @click="router.push({ name: 'mistakes' })">查看错题库</NButton>
      </NSpace>
    </div>

    <NGrid class="stats-grid" :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
      <NGi>
        <NCard :bordered="false" class="stat-card">
          <NStatistic label="错题总数" :value="analytics?.total_mistakes ?? 0" />
        </NCard>
      </NGi>
      <NGi>
        <NCard :bordered="false" class="stat-card">
          <NStatistic label="近 7 天新增" :value="analytics?.new_last_7_days ?? 0" />
        </NCard>
      </NGi>
      <NGi>
        <NCard :bordered="false" class="stat-card">
          <NStatistic label="今日待复习" :value="reviews.length" />
        </NCard>
      </NGi>
      <NGi>
        <NCard :bordered="false" class="stat-card">
          <NStatistic
            label="复习完成率"
            :value="Math.round((analytics?.review_completion_rate ?? 0) * 100)"
            suffix="%"
          />
        </NCard>
      </NGi>
    </NGrid>

    <NCard :bordered="false" class="section-card" title="待确认的试卷">
      <template #header-extra>
        <NButton text type="primary" @click="router.push({ name: 'upload' })">继续上传</NButton>
      </template>

      <NEmpty v-if="!loading && !papers.length" description="还没有试卷，先上传一份吧" />
      <div v-else class="paper-list">
        <div v-for="paper in papers" :key="paper.id" class="paper-row" @click="openPaper(paper)">
          <div class="paper-main">
            <div class="paper-title">{{ paper.title }}</div>
            <div class="paper-meta">
              <NTag size="small" :bordered="false">{{ paper.subject }}</NTag>
              <span>{{ paper.page_count }} 页</span>
              <NTag size="small" :type="statusType(paper.status)" :bordered="false">
                {{ paper.status }}
              </NTag>
            </div>
            <div v-if="paper.error_message" class="paper-error">{{ paper.error_message }}</div>
          </div>
          <NButton size="small" :disabled="paper.status !== 'ready'" type="primary" secondary>
            进入确认工作台
          </NButton>
        </div>
      </div>
    </NCard>

    <NCard :bordered="false" class="section-card" title="今日复习">
      <template #header-extra>
        <NButton text type="primary" @click="router.push({ name: 'reviews' })">全部计划</NButton>
      </template>
      <NEmpty v-if="!reviews.length" description="今天没有到期的复习，保持节奏" />
      <div v-else class="review-list">
        <div v-for="item in reviews" :key="item.plan_id" class="review-row">
          <span class="review-label">{{ item.label || '题目' }}</span>
          <span class="review-text">{{ item.text || '（无文本）' }}</span>
          <NTag size="small" :bordered="false">{{ item.subject }}</NTag>
        </div>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.stat-card {
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.section-card {
  margin-top: 18px;
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.paper-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.paper-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--mb-border);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}

.paper-row:hover {
  border-color: var(--mb-brand);
  background: var(--mb-brand-soft);
}

.paper-title {
  font-weight: 600;
  font-size: 14px;
}

.paper-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--mb-muted);
  margin-top: 4px;
}

.paper-error {
  font-size: 11px;
  color: #b45309;
  margin-top: 4px;
}

.review-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.review-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--mb-border);
  font-size: 13px;
}

.review-row:last-child {
  border-bottom: none;
}

.review-label {
  font-weight: 600;
  min-width: 70px;
}

.review-text {
  flex: 1;
  color: var(--mb-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}
</style>
