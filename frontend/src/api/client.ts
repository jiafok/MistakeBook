import axios from 'axios'
import type {
  AdminUserRow,
  AdminUserSummary,
  Analytics,
  AnalysisResult,
  CommitResult,
  KnowledgePoint,
  Mistake,
  MistakePage,
  Paper,
  PracticeItem,
  PracticeResult,
  ReviewItem,
  SystemStatus,
  TokenResponse,
  User,
  Workspace,
} from './types'

import type { AiSettings, AiTestResult } from './ai-settings-types'

const http = axios.create({ baseURL: '/api/v1', timeout: 120000 })

export const TOKEN_KEY = 'mistakebook.token'

http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      localStorage.removeItem(TOKEN_KEY)
      if (!location.pathname.startsWith('/login')) {
        location.assign('/login')
      }
    }
    return Promise.reject(error)
  },
)

export function apiErrorMessage(error: unknown): string {
  const err = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item as { msg?: string })?.msg || '')
      .filter(Boolean)
      .join('；')
  }
  return err?.message || '请求失败'
}

export const api = {
  async register(
    identifier: string,
    password: string,
    displayName: string,
  ): Promise<TokenResponse> {
    // Accepts a username or an email; the backend derives a username from an email.
    const isEmail = identifier.includes('@')
    const { data } = await http.post<TokenResponse>('/auth/register', {
      username: isEmail ? null : identifier,
      email: isEmail ? identifier : null,
      password,
      display_name: displayName,
    })
    return data
  },

  async login(identifier: string, password: string): Promise<TokenResponse> {
    const { data } = await http.post<TokenResponse>('/auth/login', { identifier, password })
    return data
  },

  async updateProfile(payload: {
    display_name?: string
    current_password?: string
    new_password?: string
  }): Promise<User> {
    const { data } = await http.patch<User>('/auth/me', payload)
    return data
  },

  async adminUsers(): Promise<AdminUserRow[]> {
    const { data } = await http.get<AdminUserRow[]>('/admin/users')
    return data
  },

  async adminUserSummary(userId: number): Promise<AdminUserSummary> {
    const { data } = await http.get<AdminUserSummary>(`/admin/users/${userId}/summary`)
    return data
  },

  async adminUserMistakes(
    userId: number,
    params: Record<string, string | number | undefined>,
  ): Promise<MistakePage> {
    const { data } = await http.get<MistakePage>(`/admin/users/${userId}/mistakes`, { params })
    return data
  },

  async adminUserPapers(userId: number): Promise<Paper[]> {
    const { data } = await http.get<Paper[]>(`/admin/users/${userId}/papers`)
    return data
  },

  async adminUserWeakPoints(
    userId: number,
  ): Promise<Array<{ name: string; mistake_count: number }>> {
    const { data } = await http.get<Array<{ name: string; mistake_count: number }>>(
      `/admin/users/${userId}/weak-points`,
    )
    return data
  },

  async me(): Promise<User> {
    const { data } = await http.get<User>('/auth/me')
    return data
  },

  async status(): Promise<SystemStatus> {
    const { data } = await http.get<SystemStatus>('/status')
    return data
  },

  async listPapers(): Promise<Paper[]> {
    const { data } = await http.get<Paper[]>('/papers')
    return data
  },

  async getPaper(id: number): Promise<Paper> {
    const { data } = await http.get<Paper>(`/papers/${id}`)
    return data
  },

  async uploadPapers(files: File[], title: string, subject: string): Promise<Paper> {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    form.append('title', title)
    form.append('subject', subject)
    const { data } = await http.post<Paper>('/papers/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  async deletePaper(id: number): Promise<void> {
    await http.delete(`/papers/${id}`)
  },

  async reanalyzePaper(id: number): Promise<Paper> {
    const { data } = await http.post<Paper>(`/papers/${id}/reanalyze`)
    return data
  },

  async workspace(paperId: number): Promise<Workspace> {
    const { data } = await http.get<Workspace>(`/papers/${paperId}/workspace`)
    return data
  },

  async saveSelections(paperId: number, selections: Record<number, boolean>): Promise<void> {
    await http.patch(`/papers/${paperId}/selections`, { selections })
  },

  async commit(paperId: number, regionIds: number[]): Promise<CommitResult> {
    const { data } = await http.post<CommitResult>(`/papers/${paperId}/commit`, {
      region_ids: regionIds,
    })
    return data
  },

  pageImageUrl(paperId: number, pageNo: number): string {
    // <img> cannot send an Authorization header, so pass the token as a query param.
    const token = localStorage.getItem(TOKEN_KEY)
    const suffix = token ? `?token=${encodeURIComponent(token)}` : ''
    return `/api/v1/papers/${paperId}/pages/${pageNo}/image${suffix}`
  },

  mistakeImageUrl(imageUrl: string): string {
    // <img> cannot send an Authorization header, so pass the token as a query param.
    if (!imageUrl) return ''
    const token = localStorage.getItem(TOKEN_KEY)
    const suffix = token ? `?token=${encodeURIComponent(token)}` : ''
    return `${imageUrl}${suffix}`
  },

  async listMistakes(params: Record<string, string | number | undefined>): Promise<MistakePage> {
    const { data } = await http.get<MistakePage>('/mistakes', { params })
    return data
  },

  async getMistake(id: number): Promise<Mistake> {
    const { data } = await http.get<Mistake>(`/mistakes/${id}`)
    return data
  },

  async updateMistake(id: number, payload: Record<string, unknown>): Promise<Mistake> {
    const { data } = await http.patch<Mistake>(`/mistakes/${id}`, payload)
    return data
  },

  async deleteMistake(id: number): Promise<void> {
    await http.delete(`/mistakes/${id}`)
  },

  async analyze(questionId: number): Promise<AnalysisResult> {
    const { data } = await http.post<AnalysisResult>('/ai/analyze', {
      question_id: questionId,
      apply: true,
    })
    return data
  },

  async practice(mistakeId: number, count: number): Promise<PracticeResult> {
    const { data } = await http.post<PracticeResult>('/ai/practice', {
      mistake_id: mistakeId,
      count,
      save: true,
    })
    return data
  },

  async listPractice(): Promise<PracticeItem[]> {
    const { data } = await http.get<PracticeItem[]>('/ai/practice')
    return data
  },

  async reviews(horizon: 'today' | 'week' | 'month'): Promise<ReviewItem[]> {
    const { data } = await http.get<ReviewItem[]>('/reviews', { params: { horizon } })
    return data
  },

  async submitReview(planId: number, result: 'pass' | 'fail' | 'skip'): Promise<void> {
    await http.post(`/reviews/${planId}/result`, { result })
  },

  async analytics(): Promise<Analytics> {
    const { data } = await http.get<Analytics>('/analytics')
    return data
  },

  async knowledgePoints(): Promise<KnowledgePoint[]> {
    const { data } = await http.get<KnowledgePoint[]>('/knowledge-points')
    return data
  },

  async exportFile(fmt: 'pdf' | 'docx' | 'markdown' | 'obsidian'): Promise<Blob> {
    const { data } = await http.get(`/export/${fmt}`, { responseType: 'blob' })
    return data as Blob
  },

  async getAiSettings(): Promise<AiSettings> {
    const { data } = await http.get<AiSettings>('/settings/ai')
    return data
  },

  async saveAiSettings(payload: {
    provider: string
    api_key?: string | null
    base_url?: string | null
    model?: string | null
    timeout_seconds?: number | null
  }): Promise<AiSettings> {
    const { data } = await http.put<AiSettings>('/settings/ai', payload)
    return data
  },

  async testAiSettings(payload: {
    provider?: string
    api_key?: string | null
    base_url?: string | null
    model?: string | null
    timeout_seconds?: number | null
  }): Promise<AiTestResult> {
    const { data } = await http.post<AiTestResult>('/settings/ai/test', payload, {
      timeout: 60000,
    })
    return data
  },
}
