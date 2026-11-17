<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NEmpty, NInput, NSpace, NSpin, NTag, NStatistic, useMessage } from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { KnowledgePoint } from '../api/types'

const router = useRouter()
const message = useMessage()

const items = ref<KnowledgePoint[]>([])
const keyword = ref('')
const loading = ref(true)

const filtered = computed(() =>
  items.value.filter((item) => {
    const needle = keyword.value.trim()
    if (!needle) return true
    return item.name.includes(needle) || item.subject.includes(needle)
  }),
)

const grouped = computed(() => {
  const map = new Map<string, KnowledgePoint[]>()
  for (const item of filtered.value) {
    const key = item.subject || '未分类'
    const list = map.get(key) ?? []
    list.push(item)
    map.set(key, list)
  }
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh-CN'))
})

function openKp(item: KnowledgePoint) {
  router.push({ name: 'mistakes', query: { knowledge_point: item.name } })
}

onMounted(async () => {
  try {
    items.value = await api.knowledgePoints()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">知识点</h2>
        <p class="mb-page-subtitle">按学科聚合，点击查看相关错题</p>
      </div>
      <NInput v-model:value="keyword" placeholder="搜索知识点" clearable style="width: 240px" />
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && !filtered.length" description="还没有知识点，先收录并分析错题" class="mb-empty" />

      <div v-else class="kp-groups">
        <NCard v-for="[subject, list] in grouped" :key="subject" :bordered="false" class="kp-card">
          <template #header>
            <div class="kp-head">
              <strong>{{ subject }}</strong>
              <span class="kp-count">{{ list.length }} 个知识点</span>
            </div>
          </template>

          <NSpace :size="10" wrap>
            <div v-for="item in list" :key="item.id" class="kp-item" @click="openKp(item)">
              <div class="kp-name">{{ item.name }}</div>
              <div class="kp-stat">
                <NStatistic :value="item.mistake_count" size="small" suffix="错题" />
              </div>
              <NTag size="tiny" :bordered="false">共 {{ item.question_count }} 题</NTag>
            </div>
          </NSpace>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.kp-groups {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.kp-card {
  border-radius: 14px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.kp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.kp-count {
  font-size: 12px;
  color: var(--mb-muted);
}

.kp-item {
  min-width: 170px;
  padding: 10px 14px;
  border: 1px solid var(--mb-border);
  border-radius: 10px;
  background: #fbfcfe;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}

.kp-item:hover {
  border-color: var(--mb-brand);
  background: var(--mb-brand-soft);
}

.kp-name {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}

.kp-stat {
  margin-bottom: 4px;
}
</style>
