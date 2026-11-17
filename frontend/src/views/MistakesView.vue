<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NInput,
  NSelect,
  NSpace,
  NTag,
  NPagination,
  NEmpty,
  NSpin,
  NDropdown,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { KnowledgePoint, Mistake } from '../api/types'

const router = useRouter()
const message = useMessage()

const items = ref<Mistake[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const loading = ref(false)

const keyword = ref('')
const subject = ref<string | null>(null)
const knowledgePoint = ref<string | null>(null)
const sort = ref('recent')

const knowledgePoints = ref<KnowledgePoint[]>([])

const subjectOptions = [
  { label: '全部学科', value: '' },
  ...['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理', '政治'].map((s) => ({
    label: s,
    value: s,
  })),
]

const sortOptions = [
  { label: '最近添加', value: 'recent' },
  { label: '最早添加', value: 'oldest' },
  { label: '错误次数最多', value: 'wrong_count' },
]

const kpOptions = computed(() => [
  { label: '全部知识点', value: '' },
  ...knowledgePoints.value.map((kp) => ({ label: `${kp.name}（${kp.mistake_count}）`, value: kp.name })),
])

const exportOptions = [
  { label: '导出 PDF', key: 'pdf' },
  { label: '导出 Word', key: 'docx' },
  { label: '导出 Markdown', key: 'markdown' },
  { label: '导出 Obsidian', key: 'obsidian' },
]

async function load() {
  loading.value = true
  try {
    const result = await api.listMistakes({
      q: keyword.value.trim() || undefined,
      subject: subject.value || undefined,
      knowledge_point: knowledgePoint.value || undefined,
      sort: sort.value,
      page: page.value,
      page_size: pageSize,
    })
    items.value = result.items
    total.value = result.total
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function reset() {
  keyword.value = ''
  subject.value = null
  knowledgePoint.value = null
  sort.value = 'recent'
  page.value = 1
  load()
}

async function doExport(key: string) {
  try {
    const blob = await api.exportFile(key as 'pdf' | 'docx' | 'markdown' | 'obsidian')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `错题本.${key === 'markdown' || key === 'obsidian' ? 'md' : key}`
    link.click()
    URL.revokeObjectURL(url)
    message.success('导出已开始下载')
  } catch (error) {
    message.error(apiErrorMessage(error))
  }
}

function openDetail(item: Mistake) {
  router.push({ name: 'mistake-detail', params: { id: item.id } })
}

function reasonColor(reason: string): 'error' | 'warning' | 'info' | 'default' {
  if (reason === '计算错误' || reason === '公式错误') return 'error'
  if (reason === '审题错误') return 'warning'
  if (reason === '概念错误' || reason === '知识点欠缺') return 'info'
  return 'default'
}

onMounted(async () => {
  await load()
  try {
    knowledgePoints.value = await api.knowledgePoints()
  } catch {
    knowledgePoints.value = []
  }
})
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">错题库</h2>
        <p class="mb-page-subtitle">共 {{ total }} 道已确认收录的错题</p>
      </div>
      <NSpace>
        <NDropdown :options="exportOptions" @select="doExport">
          <NButton>导出</NButton>
        </NDropdown>
        <NButton type="primary" @click="router.push({ name: 'upload' })">上传新试卷</NButton>
      </NSpace>
    </div>

    <NCard :bordered="false" class="filter-card">
      <NSpace align="center" :size="12" wrap>
        <NInput
          v-model:value="keyword"
          placeholder="搜索题干关键字"
          clearable
          style="width: 240px"
          @keyup.enter="search"
        />
        <NSelect
          v-model:value="subject"
          :options="subjectOptions"
          placeholder="学科"
          clearable
          style="width: 140px"
        />
        <NSelect
          v-model:value="knowledgePoint"
          :options="kpOptions"
          placeholder="知识点"
          clearable
          filterable
          style="width: 200px"
        />
        <NSelect v-model:value="sort" :options="sortOptions" style="width: 150px" />
        <NButton type="primary" secondary @click="search">筛选</NButton>
        <NButton quaternary @click="reset">重置</NButton>
      </NSpace>
    </NCard>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && !items.length" description="没有符合条件的错题" class="mb-empty" />

      <div v-else class="mistake-grid">
        <NCard
          v-for="item in items"
          :key="item.id"
          :bordered="false"
          class="mistake-card"
          hoverable
          @click="openDetail(item)"
        >
          <div class="card-head">
            <span class="card-label">{{ item.label || '题目' }}</span>
            <NTag size="small" :bordered="false">{{ item.subject }}</NTag>
          </div>

          <div class="card-text mb-text-clamp">{{ item.text || '（无识别文本）' }}</div>

          <div class="card-meta">
            <NTag v-if="item.error_reason" size="small" :type="reasonColor(item.error_reason)" :bordered="false">
              {{ item.error_reason }}
            </NTag>
            <NTag v-for="kp in item.knowledge_points.slice(0, 2)" :key="kp" size="small" :bordered="false" type="info">
              {{ kp }}
            </NTag>
          </div>

          <div class="card-foot">
            <span>难度 {{ item.difficulty }}/5</span>
            <span>错误 {{ item.wrong_count }} 次</span>
            <span v-if="item.paper_title" class="card-paper">{{ item.paper_title }}</span>
          </div>
        </NCard>
      </div>

      <NSpace v-if="total > pageSize" justify="center" style="margin-top: 22px">
        <NPagination
          v-model:page="page"
          :page-size="pageSize"
          :item-count="total"
          @update:page="load"
        />
      </NSpace>
    </NSpin>
  </div>
</template>

<style scoped>
.filter-card {
  border-radius: 14px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.mistake-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.mistake-card {
  border-radius: 14px;
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}

.mistake-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.1);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.card-label {
  font-weight: 650;
  font-size: 13px;
}

.card-text {
  font-size: 13px;
  line-height: 1.65;
  color: #334155;
  min-height: 4.9em;
}

.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 10px 0 8px;
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  color: var(--mb-muted);
  border-top: 1px dashed var(--mb-border);
  padding-top: 8px;
}

.card-paper {
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 40%;
}
</style>
