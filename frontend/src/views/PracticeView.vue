<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NSelect, NSpace, NSpin, NTag, useMessage } from 'naive-ui'
import { api, apiErrorMessage } from '../api/client'
import type { Mistake, PracticeItem } from '../api/types'

const router = useRouter()
const message = useMessage()

const mistakes = ref<Mistake[]>([])
const selectedMistake = ref<number | null>(null)
const count = ref(5)
const generating = ref(false)
const practiceItems = ref<PracticeItem[]>([])
const historyItems = ref<PracticeItem[]>([])
const loading = ref(true)

const countOptions = [5, 10, 20].map((n) => ({ label: `${n} 题`, value: n }))

const mistakeOptions = ref<Array<{ label: string; value: number }>>([])

async function load() {
  loading.value = true
  try {
    const [page, history] = await Promise.all([api.listMistakes({ page_size: 100 }), api.listPractice()])
    mistakes.value = page.items
    mistakeOptions.value = page.items.map((item) => ({
      label: `${item.label || '题目'} · ${item.subject}`,
      value: item.id,
    }))
    if (page.items.length) selectedMistake.value = page.items[0].id
    historyItems.value = history
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function generate() {
  if (!selectedMistake.value) {
    message.warning('请先选择一道错题')
    return
  }
  generating.value = true
  try {
    const result = await api.practice(selectedMistake.value, count.value)
    practiceItems.value = result.questions
    if (!result.questions.length) {
      message.warning(result.error || '未能生成练习题，请先在设置中配置 AI 提供方')
      return
    }
    message.success(`已生成 ${result.questions.length} 道练习题`)
    historyItems.value = await api.listPractice()
  } catch (error) {
    message.error(apiErrorMessage(error))
  } finally {
    generating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-page">
    <div class="mb-page-header">
      <div>
        <h2 class="mb-page-title">AI 练习</h2>
        <p class="mb-page-subtitle">基于历史错题生成同类型练习，检验是否真正掌握</p>
      </div>
      <NButton @click="router.push({ name: 'settings' })">配置 AI 提供方</NButton>
    </div>

    <NCard :bordered="false" class="gen-card" title="生成练习题">
      <NSpin :show="loading">
        <NEmpty v-if="!loading && !mistakes.length" description="还没有错题，先上传试卷并确认收录">
          <template #extra>
            <NButton type="primary" @click="router.push({ name: 'upload' })">上传试卷</NButton>
          </template>
        </NEmpty>

        <NSpace v-else align="center" :size="12" wrap>
          <NSelect
            v-model:value="selectedMistake"
            :options="mistakeOptions"
            filterable
            placeholder="选择一道错题"
            style="width: 320px"
          />
          <NSelect v-model:value="count" :options="countOptions" style="width: 120px" />
          <NButton type="primary" :loading="generating" :disabled="!selectedMistake" @click="generate">
            生成练习题
          </NButton>
        </NSpace>
      </NSpin>
    </NCard>

    <NCard v-if="practiceItems.length" :bordered="false" class="list-card" title="本次生成">
      <div class="practice-list">
        <div v-for="(item, index) in practiceItems" :key="index" class="practice-item">
          <div class="practice-head">
            <strong>练习 {{ index + 1 }}</strong>
            <NTag v-if="item.knowledge_point" size="small" :bordered="false" type="info">
              {{ item.knowledge_point }}
            </NTag>
          </div>
          <div class="practice-text">{{ item.text }}</div>
          <details class="practice-answer">
            <summary>查看答案与解析</summary>
            <p><strong>答案：</strong>{{ item.answer }}</p>
            <p><strong>解析：</strong>{{ item.explanation }}</p>
          </details>
        </div>
      </div>
    </NCard>

    <NCard :bordered="false" class="list-card" title="历史练习">
      <NEmpty v-if="!historyItems.length" description="还没有生成过练习题" />
      <div v-else class="practice-list">
        <div v-for="(item, index) in historyItems" :key="index" class="practice-item">
          <div class="practice-head">
            <strong>历史 {{ index + 1 }}</strong>
            <NTag v-if="item.knowledge_point" size="small" :bordered="false" type="info">
              {{ item.knowledge_point }}
            </NTag>
          </div>
          <div class="practice-text">{{ item.text }}</div>
          <details class="practice-answer">
            <summary>查看答案与解析</summary>
            <p><strong>答案：</strong>{{ item.answer }}</p>
            <p><strong>解析：</strong>{{ item.explanation }}</p>
          </details>
        </div>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.gen-card,
.list-card {
  border-radius: 14px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.05);
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

.practice-text {
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  color: #334155;
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

.practice-answer p {
  margin: 6px 0 0;
  line-height: 1.7;
}
</style>
