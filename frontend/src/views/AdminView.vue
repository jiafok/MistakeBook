<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NEmpty,
  NGi,
  NGrid,
  NProgress,
  NSpin,
  NStatistic,
  NTag,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { AdminUserRow, AdminUserSummary, Mistake, Paper } from '../api/types'

const message = useMessage()

const loading = ref(true)
const users = ref<AdminUserRow[]>([])
const selectedId = ref<number | null>(null)
const summary = ref<AdminUserSummary | null>(null)
const papers = ref<Paper[]>([])
const mistakes = ref<Mistake[]>([])
const weakPoints = ref<Array<{ name: string; mistake_count: number }>>([])
const detailLoading = ref(false)

const selectedUser = computed(() => users.value.find((u) => u.id === selectedId.value) ?? null)

const totalMistakes = computed(() => users.value.reduce((sum, u) => sum + u.mistake_count, 0))
const totalPapers = computed(() => users.value.reduce((sum, u) => sum + u.paper_count, 0))

const columns: DataTableColumns<AdminUserRow> = [
  {
    title: '账号',
    key: 'username',
    render(row) {
      return `${row.username}${row.is_self ? '（我）' : ''}`
    },
  },
  { title: '昵称', key: 'display_name' },
  {
    title: '角色',
    key: 'role',
    render(row) {
      return row.role === 'admin' ? '管理员' : '成员'
    },
  },
  {
    title: '状态',
    key: 'is_active',
    render(row) {
      return row.is_active ? '正常' : '已停用'
    },
  },
  { title: '试卷', key: 'paper_count', align: 'right' },
  { title: '错题', key: 'mistake_count', align: 'right' },
  { title: '待复习', key: 'pending_review_count', align: 'right' },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return row.is_self ? '—' : '查看'
    },
  },
]

const paperColumns: DataTableColumns<Paper> = [
  { title: '试卷', key: 'title' },
  { title: '学科', key: 'subject' },
  { title: '状态', key: 'status' },
  { title: '页数', key: 'page_count', align: 'right' },
  { title: '上传时间', key: 'created_at' },
]

const mistakeColumns: DataTableColumns<Mistake> = [
  { title: '题号', key: 'label' },
  { title: '学科', key: 'subject' },
  {
    title: '题目',
    key: 'text',
    ellipsis: { tooltip: true },
  },
  {
    title: '错误次数',
    key: 'wrong_count',
    align: 'right',
  },
  { title: '状态', key: 'status' },
]

async function loadUsers() {
  loading.value = true
  try {
    users.value = await api.adminUsers()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function openUser(row: AdminUserRow) {
  if (row.is_self) return
  selectedId.value = row.id
  detailLoading.value = true
  summary.value = null
  papers.value = []
  mistakes.value = []
  weakPoints.value = []
  try {
    const [sum, paperList, mistakePage, weak] = await Promise.all([
      api.adminUserSummary(row.id),
      api.adminUserPapers(row.id),
      api.adminUserMistakes(row.id, { page: 1, page_size: 20 }),
      api.adminUserWeakPoints(row.id),
    ])
    summary.value = sum
    papers.value = paperList
    mistakes.value = mistakePage.items
    weakPoints.value = weak
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

function closeUser() {
  selectedId.value = null
  summary.value = null
  papers.value = []
  mistakes.value = []
  weakPoints.value = []
}

onMounted(loadUsers)
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">账号监管</h2>
        <p class="mb-page-subtitle">
          管理员可查看其他成员的学习情况；出于数据归属原则，监管为只读，不会修改成员内容。
        </p>
      </div>
      <NButton secondary @click="loadUsers">刷新</NButton>
    </div>

    <NAlert type="info" :bordered="false" class="tip">
      不同使用者的数据相互隔离：每个账号只能看到自己的试卷与错题，管理员额外获得只读查看权限。
    </NAlert>

    <NGrid :cols="3" :x-gap="16" :y-gap="16" responsive="screen" item-responsive class="stats">
      <NGi>
        <NCard :bordered="false"><NStatistic label="账号总数" :value="users.length" /></NCard>
      </NGi>
      <NGi>
        <NCard :bordered="false"><NStatistic label="试卷总数" :value="totalPapers" /></NCard>
      </NGi>
      <NGi>
        <NCard :bordered="false"><NStatistic label="错题总数" :value="totalMistakes" /></NCard>
      </NGi>
    </NGrid>

    <NSpin :show="loading">
      <NCard :bordered="false" title="全部账号">
        <NDataTable
          v-if="users.length"
          :columns="columns"
          :data="users"
          :row-key="(row: AdminUserRow) => row.id"
          :row-props="(row: AdminUserRow) => ({
            style: row.is_self ? '' : 'cursor: pointer',
            onClick: () => openUser(row),
          })"
          :bordered="false"
          size="small"
          :scroll-x="820"
        />
        <NEmpty v-else description="暂无账号" />
      </NCard>
    </NSpin>

    <NSpin :show="detailLoading">
      <div v-if="selectedUser && !selectedUser.is_self" class="detail">
        <NCard :bordered="false">
          <template #header>
            <div class="detail-head">
              <span>{{ selectedUser.display_name || selectedUser.username }} 的学习概览</span>
              <NButton size="tiny" quaternary @click="closeUser">收起</NButton>
            </div>
          </template>

          <NDescriptions v-if="summary" :column="3" size="small" bordered>
            <NDescriptionsItem label="用户名">{{ summary.user.username }}</NDescriptionsItem>
            <NDescriptionsItem label="邮箱">{{ summary.user.email || '未设置' }}</NDescriptionsItem>
            <NDescriptionsItem label="角色">
              {{ summary.user.role === 'admin' ? '管理员' : '成员' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="试卷数">{{ summary.paper_count }}</NDescriptionsItem>
            <NDescriptionsItem label="有效错题">{{ summary.mistake_count }}</NDescriptionsItem>
            <NDescriptionsItem label="已归档">{{ summary.archived_count }}</NDescriptionsItem>
            <NDescriptionsItem label="复习计划">{{ summary.review_plan_count }}</NDescriptionsItem>
            <NDescriptionsItem label="已完成">{{ summary.review_done_count }}</NDescriptionsItem>
            <NDescriptionsItem label="完成率">
              <div class="rate">
                <NProgress
                  type="line"
                  :percentage="Math.round(summary.review_completion_rate * 100)"
                  :show-indicator="false"
                  :height="8"
                />
                <span>{{ Math.round(summary.review_completion_rate * 100) }}%</span>
              </div>
            </NDescriptionsItem>
          </NDescriptions>

          <div v-if="Object.keys(summary?.subject_distribution ?? {}).length" class="subjects">
            <NTag
              v-for="(count, subject) in summary?.subject_distribution"
              :key="subject"
              size="small"
              round
              :bordered="false"
              type="info"
            >
              {{ subject }} · {{ count }}
            </NTag>
          </div>
        </NCard>

        <NCard :bordered="false" title="薄弱知识点">
          <div v-if="weakPoints.length" class="subjects">
            <NTag
              v-for="point in weakPoints"
              :key="point.name"
              size="small"
              round
              :bordered="false"
              type="warning"
            >
              {{ point.name }} · {{ point.mistake_count }}
            </NTag>
          </div>
          <NEmpty v-else size="small" description="暂无知识点数据" />
        </NCard>

        <NCard :bordered="false" title="试卷">
          <NDataTable
            v-if="papers.length"
            :columns="paperColumns"
            :data="papers"
            :row-key="(row: Paper) => row.id"
            :bordered="false"
            size="small"
            :scroll-x="640"
          />
          <NEmpty v-else size="small" description="暂无试卷" />
        </NCard>

        <NCard :bordered="false" title="最近错题">
          <NDataTable
            v-if="mistakes.length"
            :columns="mistakeColumns"
            :data="mistakes"
            :row-key="(row: Mistake) => row.id"
            :bordered="false"
            size="small"
            :scroll-x="720"
          />
          <NEmpty v-else size="small" description="暂无错题" />
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.tip {
  margin-bottom: 16px;
}

.stats {
  margin-bottom: 16px;
}

.detail {
  display: grid;
  gap: 16px;
  margin-top: 16px;
}

.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.subjects {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.rate {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rate :deep(.n-progress) {
  flex: 1;
  min-width: 60px;
}
</style>
