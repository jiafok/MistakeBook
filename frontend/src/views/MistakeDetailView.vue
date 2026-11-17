<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NDivider,
  NEmpty,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
} from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { Mistake, PracticeResult } from '../api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const id = computed(() => Number(route.params.id))
const mistake = ref<Mistake | null>(null)
const loading = ref(true)
const analyzing = ref(false)
const editing = ref(false)

const editSubject = ref('')
const editDifficulty = ref(3)
const editSolution = ref('')
const editKps = ref('')
const editTags = ref('')

const practiceCount = ref(5)
const practiceLoading = ref(false)
const practice = ref<PracticeResult | null>(null)

const subjectOptions = ['数学', '语文', '英语', '物理', '化学', '生物', '历史', '地理', '政治', '未分类'].map(
  (s) => ({ label: s, value: s }),
)
const countOptions = [5, 10, 20].map((n) => ({ label: `${n} 题`, value: n }))

async function load() {
  loading.value = true
  try {
    mistake.value = await api.getMistake(id.value)
    editSubject.value = mistake.value.subject
    editDifficulty.value = mistake.value.difficulty
    editSolution.value = mistake.value.correct_solution
    editKps.value = mistake.value.knowledge_points.join('、')
    editTags.value = mistake.value.tags.join('、')
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

function splitList(raw: string): string[] {
  return raw
    .split(/[,，、;；]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function save() {
  if (!mistake.value) return
  try {
    mistake.value = await api.updateMistake(mistake.value.id, {
      subject: editSubject.value,
      difficulty: editDifficulty.value,
      correct_solution: editSolution.value,
      knowledge_points: splitList(editKps.value),
      tags: splitList(editTags.value),
    })
    editing.value = false
    message.success('已保存')
  } catch (error) {
    message.error(apiErrorMessage(error))
  }
}

async function runAnalysis() {
  if (!mistake.value) return
  analyzing.value = true
  try {
    const result = await api.analyze(mistake.value.question_id)
    if (result.is_fallback) {
      message.warning(result.error || '已使用本地规则分析')
    } else {
      message.success(`已由 ${result.provider} 完成分析`)
    }
    await load()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    analyzing.value = false
  }
}

async function generatePractice() {
  if (!mistake.value) return
  practiceLoading.value = true
  try {
    const result = await api.practice(mistake.value.id, practiceCount.value)
    practice.value = result
    if (!result.questions.length) {
      message.warning(result.error || '未能生成练习题')
    } else {
      message.success(`已生成 ${result.questions.length} 道练习题`)
    }
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    practiceLoading.value = false
  }
}

async function remove() {
  if (!mistake.value) return
  try {
    await api.deleteMistake(mistake.value.id)
    message.success('已删除')
    router.push({ name: 'mistakes' })
  } catch (error) {
    message.error(apiErrorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <NButton size="small" quaternary @click="router.push({ name: 'mistakes' })">← 返回错题库</NButton>
        <h2 class="mb-page-title" style="margin-top: 8px">
          {{ mistake?.label || '题目详情' }}
        </h2>
      </div>
      <NSpace v-if="mistake">
        <NButton :loading="analyzing" @click="runAnalysis">AI 重新分析</NButton>
        <NButton v-if="!editing" type="primary" @click="editing = true">编辑</NButton>
        <NButton v-else type="primary" @click="save">保存</NButton>
        <NButton quaternary type="error" @click="remove">删除</NButton>
      </NSpace>
    </div>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && !mistake" description="题目不存在" />

      <template v-else-if="mistake">
        <NCard :bordered="false" class="detail-card" title="题目内容">
          <div class="question-text">{{ mistake.text || '（无识别文本）' }}</div>
          <template v-if="mistake.answer_text">
            <NDivider style="margin: 14px 0" />
            <div class="answer-label">学生答案</div>
            <div class="answer-text">{{ mistake.answer_text }}</div>
          </template>
        </NCard>

        <NCard :bordered="false" class="detail-card" title="基础信息">
          <template v-if="editing">
            <NSpace vertical size="medium">
              <div class="field">
                <label>学科</label>
                <NSelect v-model:value="editSubject" :options="subjectOptions" />
              </div>
              <div class="field">
                <label>难度（1–5）</label>
                <NInputNumber v-model:value="editDifficulty" :min="1" :max="5" />
              </div>
              <div class="field">
                <label>知识点（用顿号或逗号分隔）</label>
                <NInput v-model:value="editKps" placeholder="例如：一元二次方程、因式分解" />
              </div>
              <div class="field">
                <label>标签</label>
                <NInput v-model:value="editTags" placeholder="例如：易错、期中" />
              </div>
              <div class="field">
                <label>正确解法</label>
                <NInput v-model:value="editSolution" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" />
              </div>
            </NSpace>
          </template>

          <NDescriptions v-else :column="2" label-placement="left" bordered>
            <NDescriptionsItem label="学科">{{ mistake.subject }}</NDescriptionsItem>
            <NDescriptionsItem label="难度">{{ mistake.difficulty }} / 5</NDescriptionsItem>
            <NDescriptionsItem label="错误次数">{{ mistake.wrong_count }}</NDescriptionsItem>
            <NDescriptionsItem label="来源试卷">{{ mistake.paper_title || '—' }}</NDescriptionsItem>
            <NDescriptionsItem label="错题概率">
              {{ mistake.wrong_probability ?? '—' }}
            </NDescriptionsItem>
            <NDescriptionsItem label="收录时间">
              {{ new Date(mistake.created_at).toLocaleString() }}
            </NDescriptionsItem>
            <NDescriptionsItem label="知识点" :span="2">
              <NSpace :size="4">
                <NTag v-for="kp in mistake.knowledge_points" :key="kp" size="small" :bordered="false" type="info">
                  {{ kp }}
                </NTag>
                <span v-if="!mistake.knowledge_points.length" class="muted">未标注</span>
              </NSpace>
            </NDescriptionsItem>
            <NDescriptionsItem label="标签" :span="2">
              <NSpace :size="4">
                <NTag v-for="tag in mistake.tags" :key="tag" size="small" :bordered="false">
                  {{ tag }}
                </NTag>
                <span v-if="!mistake.tags.length" class="muted">未标注</span>
              </NSpace>
            </NDescriptionsItem>
          </NDescriptions>
        </NCard>

        <NCard :bordered="false" class="detail-card" title="错因分析">
          <NEmpty v-if="!mistake.error_reason && !mistake.correct_solution" description="还没有 AI 分析，点击右上角「AI 重新分析」" />
          <template v-else>
            <NSpace :size="8" style="margin-bottom: 10px">
              <NTag v-if="mistake.error_reason" type="error" :bordered="false">
                {{ mistake.error_reason }}
              </NTag>
            </NSpace>
            <p v-if="mistake.error_detail" class="body-text">{{ mistake.error_detail }}</p>

            <template v-if="mistake.correct_solution">
              <NDivider style="margin: 12px 0" />
              <div class="answer-label">正确解法</div>
              <p class="body-text">{{ mistake.correct_solution }}</p>
            </template>

            <template v-if="mistake.solution_steps.length">
              <NDivider style="margin: 12px 0" />
              <div class="answer-label">解题步骤</div>
              <ol class="steps">
                <li v-for="(step, index) in mistake.solution_steps" :key="index">{{ step }}</li>
              </ol>
            </template>
          </template>
        </NCard>

        <NCard :bordered="false" class="detail-card" title="同类型练习">
          <NSpace align="center" style="margin-bottom: 12px">
            <NSelect v-model:value="practiceCount" :options="countOptions" style="width: 120px" />
            <NButton type="primary" secondary :loading="practiceLoading" @click="generatePractice">
              生成练习题
            </NButton>
          </NSpace>

          <NEmpty v-if="!practice || !practice.questions.length" description="按需生成 5 / 10 / 20 道同类题" />
          <div v-else class="practice-list">
            <div v-for="(item, index) in practice.questions" :key="index" class="practice-item">
              <div class="practice-head">
                <strong>练习 {{ index + 1 }}</strong>
                <NTag v-if="item.knowledge_point" size="small" :bordered="false" type="info">
                  {{ item.knowledge_point }}
                </NTag>
              </div>
              <div class="body-text">{{ item.text }}</div>
              <details class="practice-answer">
                <summary>查看答案与解析</summary>
                <div class="body-text"><strong>答案：</strong>{{ item.answer }}</div>
                <div class="body-text"><strong>解析：</strong>{{ item.explanation }}</div>
              </details>
            </div>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.detail-card {
  border-radius: 14px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.question-text {
  font-size: 14px;
  line-height: 1.9;
  white-space: pre-wrap;
  word-break: break-word;
}

.answer-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}

.answer-text,
.body-text {
  font-size: 13px;
  line-height: 1.8;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
}

.steps {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: #334155;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field label {
  font-size: 13px;
  color: #475569;
}

.muted {
  font-size: 12px;
  color: #9aa3af;
}

.practice-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.practice-item {
  border: 1px solid var(--mb-border);
  border-radius: 10px;
  padding: 12px 14px;
  background: #fbfcfe;
}

.practice-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.practice-answer {
  margin-top: 8px;
  font-size: 12px;
  color: #475569;
}

.practice-answer summary {
  cursor: pointer;
  color: var(--mb-brand);
}
</style>
