<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
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
const previewSrc = ref('')

// Drawings lifted out of the paper; the question body itself is always text so it
// can be re-typeset and printed with the rest of the mistake book.
const figures = computed(() =>
  (mistake.value?.figures ?? []).map((figure) => ({
    ...figure,
    src: api.authImageUrl(figure.url),
  })),
)

// Whole-question screenshots only exist on mistakes recorded before figures were
// split out, so they are shown as a fallback with a note.
const legacyImage = computed(() => api.authImageUrl(mistake.value?.image_url ?? ''))

const editSubject = ref('')
const editDifficulty = ref(3)
const editSolution = ref('')
const editKps = ref('')
const editTags = ref('')
const repairing = ref(false)

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

/**
 * A record made by an older build has no pictures, only a screenshot of the whole
 * question. Its drawings can be located again from the stored page, which is what
 * turns an unprintable screenshot back into text plus figures.
 */
async function repairLegacyFigures() {
  if (!mistake.value?.paper_id) return
  repairing.value = true
  try {
    const result = await api.repairFigures(mistake.value.paper_id)
    if (result.figures > 0) {
      message.success(`已找出 ${result.figures} 张插图，并更新了 ${result.mistakes} 条错题`)
    } else {
      message.warning('这道题在试卷里没有可裁出的插图，题干文字已经可以正常打印')
    }
    await load()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    repairing.value = false
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
        <NCard :bordered="false" class="detail-card" title="题干">
          <!-- The question body is text, not a screenshot, so it can be re-typeset
               for printing. Only the drawings are lifted out of the paper. -->
          <NAlert
            v-if="!mistake.region_id"
            type="warning"
            :bordered="false"
            title="这道题的原文可能不准确"
            style="margin-bottom: 12px"
          >
            它最初是在旧版本里收录的，当时切题按整栏划分，无法判断对应的是哪一道题。
            建议到「框选题目」页重新选中这道题，或直接在上方「编辑」里改成正确题干。
          </NAlert>
          <div class="question-text">{{ mistake.text || '（无识别文本）' }}</div>

          <template v-if="figures.length">
            <NDivider style="margin: 14px 0" />
            <div class="answer-label">题目插图（{{ figures.length }} 张）</div>
            <div class="figure-list">
              <figure v-for="figure in figures" :key="figure.index" class="figure-item">
                <img
                  :src="figure.src"
                  :alt="`${mistake.label || '题目'} 插图 ${figure.index + 1}`"
                  :width="figure.width || undefined"
                  :height="figure.height || undefined"
                  loading="lazy"
                  @click="previewSrc = figure.src"
                />
                <figcaption>图 {{ figure.index + 1 }}</figcaption>
              </figure>
            </div>
          </template>

          <template v-else-if="legacyImage">
            <NDivider style="margin: 14px 0" />
            <NAlert type="info" :bordered="false" title="这张图来自旧版本的整题截图">
              旧记录把整道题存成了图片，打印时无法重新排版。点下面的按钮会从原试卷里重新找出插图，
              之后这道题就是「题干文字 + 插图」，可以直接打印。
            </NAlert>
            <NSpace style="margin: 10px 0">
              <NButton
                size="small"
                type="primary"
                :loading="repairing"
                :disabled="!mistake.paper_id"
                @click="repairLegacyFigures"
              >
                重新找出插图
              </NButton>
            </NSpace>
          </template>

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

    <!-- Full-screen view so small print and figure labels stay readable on a phone. -->
    <div v-if="previewSrc" class="image-preview" @click="previewSrc = ''">
      <img :src="previewSrc" :alt="mistake?.label || '原题'" />
      <div class="preview-hint">点击任意处关闭</div>
    </div>
  </div>
</template>

<style scoped>
.detail-card {
  border-radius: 14px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
}

.image-card :deep(.n-card-content) {
  /* A legacy full-question screenshot is the point of that card: give it the width. */
  padding: 8px;
}

.figure-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 4px;
}

.figure-item {
  margin: 0;
  padding: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  text-align: center;
}

.figure-item img {
  display: block;
  max-width: 100%;
  max-height: 320px;
  width: auto;
  height: auto;
  margin: 0 auto;
  cursor: zoom-in;
}

.figure-item figcaption {
  margin-top: 6px;
  font-size: 12px;
  color: #94a3b8;
}

.question-image-wrap {
  text-align: center;
}

.question-image {
  display: block;
  width: 100%;
  max-width: 100%;
  height: auto;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  cursor: zoom-in;
}

.image-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #94a3b8;
}

.image-preview {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 16px;
  background: rgba(15, 23, 42, 0.88);
  cursor: zoom-out;
  overflow: auto;
}

.image-preview img {
  max-width: 100%;
  max-height: calc(100vh - 80px);
  object-fit: contain;
  border-radius: 8px;
  background: #fff;
}

.preview-hint {
  font-size: 12px;
  color: #cbd5e1;
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
  color: #9aa3baf;
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
