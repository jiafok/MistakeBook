<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NButton, NCard, NEmpty, NGi, NGrid, NProgress, NSpin, NStatistic, NTag, useMessage } from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { Analytics } from '../api/types'

const message = useMessage()
const data = ref<Analytics | null>(null)
const loading = ref(true)

const subjectEntries = computed(() => Object.entries(data.value?.by_subject ?? {}).sort((a, b) => b[1] - a[1]))
const subjectMax = computed(() => Math.max(1, ...subjectEntries.value.map(([, v]) => v)))

const reasonEntries = computed(() =>
  Object.entries(data.value?.error_reasons ?? {}).sort((a, b) => b[1] - a[1]),
)
const reasonMax = computed(() => Math.max(1, ...reasonEntries.value.map(([, v]) => v)))

const repeatRows = computed(
  () => (data.value?.repeat_wrong ?? []) as Array<{ label?: string; subject?: string; wrong_count?: number }>,
)

const weakRows = computed(
  () =>
    (data.value?.weak_knowledge_points ?? []) as Array<{
      name?: string
      mistake_count?: number
      review_failures?: number
      recent_30d?: number
      reason?: string
    }>,
)

async function load() {
  loading.value = true
  try {
    data.value = await api.analytics()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">数据统计</h2>
        <p class="mb-page-subtitle">发现反复薄弱的知识点，比单纯刷题更重要</p>
      </div>
      <NButton @click="load">刷新</NButton>
    </div>

    <NSpin :show="loading">
      <NGrid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
        <NGi>
          <NCard :bordered="false" class="stat-card">
            <NStatistic label="错题总数" :value="data?.total_mistakes ?? 0" />
          </NCard>
        </NGi>
        <NGi>
          <NCard :bordered="false" class="stat-card">
            <NStatistic label="近 7 天新增" :value="data?.new_last_7_days ?? 0" />
          </NCard>
        </NGi>
        <NGi>
          <NCard :bordered="false" class="stat-card">
            <NStatistic
              label="复习完成率"
              :value="Math.round((data?.review_completion_rate ?? 0) * 100)"
              suffix="%"
            />
          </NCard>
        </NGi>
        <NGi>
          <NCard :bordered="false" class="stat-card">
            <NStatistic label="长期薄弱知识点" :value="weakRows.length" />
          </NCard>
        </NGi>
      </NGrid>

      <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" item-responsive class="grid-gap">
        <NGi>
          <NCard :bordered="false" class="panel-card" title="学科分布">
            <NEmpty v-if="!subjectEntries.length" description="暂无数据" />
            <div v-else class="bar-list">
              <div v-for="[name, count] in subjectEntries" :key="name" class="bar-row">
                <span class="bar-label">{{ name }}</span>
                <NProgress
                  type="line"
                  :percentage="Math.round((count / subjectMax) * 100)"
                  :show-indicator="false"
                  :height="10"
                  color="#2563eb"
                />
                <span class="bar-value">{{ count }}</span>
              </div>
            </div>
          </NCard>
        </NGi>

        <NGi>
          <NCard :bordered="false" class="panel-card" title="错误原因分布">
            <NEmpty v-if="!reasonEntries.length" description="暂无分析数据，可在题目详情中运行 AI 分析" />
            <div v-else class="bar-list">
              <div v-for="[name, count] in reasonEntries" :key="name" class="bar-row">
                <span class="bar-label">{{ name }}</span>
                <NProgress
                  type="line"
                  :percentage="Math.round((count / reasonMax) * 100)"
                  :show-indicator="false"
                  :height="10"
                  color="#f0b429"
                />
                <span class="bar-value">{{ count }}</span>
              </div>
            </div>
          </NCard>
        </NGi>
      </NGrid>

      <NGrid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" item-responsive class="grid-gap">
        <NGi>
          <NCard :bordered="false" class="panel-card" title="重复错误排行">
            <NEmpty v-if="!repeatRows.length" description="暂无题目错误 3 次以上" />
            <div v-else class="rank-list">
              <div v-for="(row, index) in repeatRows" :key="index" class="rank-row">
                <span class="rank-no">{{ index + 1 }}</span>
                <span class="rank-label">{{ row.label || '题目' }}</span>
                <NTag size="small" :bordered="false">{{ row.subject || '未分类' }}</NTag>
                <span class="rank-value">{{ row.wrong_count }} 次</span>
              </div>
            </div>
          </NCard>
        </NGi>

        <NGi>
          <NCard :bordered="false" class="panel-card" title="长期薄弱知识点">
            <NEmpty v-if="!weakRows.length" description="暂无连续薄弱的知识点" />
            <div v-else class="rank-list">
              <div v-for="(row, index) in weakRows" :key="index" class="rank-row">
                <span class="rank-no">{{ index + 1 }}</span>
                <span class="rank-label">{{ row.name }}</span>
                <NTag size="small" type="warning" :bordered="false">{{ row.reason }}</NTag>
                <span class="rank-value">{{ row.mistake_count }} 题 / 失败 {{ row.review_failures }}</span>
              </div>
            </div>
          </NCard>
        </NGi>
      </NGrid>
    </NSpin>
  </div>
</template>

<style scoped>
.stat-card,
.panel-card {
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.grid-gap {
  margin-top: 16px;
}

.bar-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bar-row {
  display: grid;
  grid-template-columns: 88px 1fr 40px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.bar-label {
  color: #475569;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-value {
  text-align: right;
  font-weight: 600;
  color: #1f2937;
}

.rank-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rank-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--mb-border);
}

.rank-row:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.rank-no {
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--mb-brand-soft);
  color: var(--mb-brand);
  font-weight: 700;
  font-size: 11px;
}

.rank-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-value {
  color: var(--mb-muted);
}
</style>
