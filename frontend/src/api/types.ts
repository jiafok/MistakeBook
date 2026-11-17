export interface User {
  id: number
  username: string
  email: string | null
  display_name: string
  role: string
  is_active?: boolean
}

export interface AdminUserRow {
  id: number
  username: string
  email: string | null
  display_name: string
  role: string
  is_active: boolean
  created_at: string
  paper_count: number
  mistake_count: number
  pending_review_count: number
  is_self: boolean
}

export interface AdminUserSummary {
  user: User
  paper_count: number
  mistake_count: number
  archived_count: number
  subject_distribution: Record<string, number>
  review_plan_count: number
  review_done_count: number
  review_completion_rate: number
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

export interface Paper {
  id: number
  title: string
  subject: string
  /** 'auto' when inferred from OCR text, 'user' when explicitly chosen */
  subject_source?: string
  status: string
  error_message: string
  page_count: number
  ocr_provider: string
  created_at: string
}

export interface PageInfo {
  id: number
  page_no: number
  width: number
  height: number
  image_url: string
}

export type Band = 'recommended' | 'suspected' | 'normal' | 'unknown'

export interface Region {
  id: number
  page_no: number
  page_id: number
  x: number
  y: number
  width: number
  height: number
  region_order: number
  label: string
  text: string
  wrong_probability: number | null
  band: Band
  detection_status: string
  is_selected: boolean
  is_recorded: boolean
}

export interface WorkspaceCounts {
  selected: number
  new: number
  existing: number
  total: number
}

export interface Workspace {
  paper: Paper
  pages: PageInfo[]
  regions: Region[]
  counts: WorkspaceCounts
}

export interface CommitItemResult {
  region_id: number
  status: 'created' | 'existing' | 'failed'
  mistake_id: number | null
  reason: string
}

export interface CommitResult {
  created: number
  existing: number
  failed: number
  items: CommitItemResult[]
}

export interface Mistake {
  id: number
  question_id: number
  label: string
  text: string
  answer_text: string
  subject: string
  difficulty: number
  knowledge_points: string[]
  tags: string[]
  error_reason: string
  error_detail: string
  correct_solution: string
  solution_steps: string[]
  wrong_count: number
  wrong_probability: number | null
  status: string
  paper_title: string
  created_at: string
}

export interface MistakePage {
  total: number
  page: number
  page_size: number
  items: Mistake[]
}

export interface AnalysisResult {
  provider: string
  model: string
  is_fallback: boolean
  error: string
  subject: string
  knowledge_points: string[]
  difficulty: number
  error_reason: string
  error_detail: string
  correct_solution: string
  solution_steps: string[]
}

export interface PracticeItem {
  text: string
  answer: string
  explanation: string
  knowledge_point: string
}

export interface PracticeResult {
  provider: string
  model: string
  is_fallback: boolean
  error: string
  questions: PracticeItem[]
}

export interface ReviewItem {
  plan_id: number
  mistake_id: number
  label: string
  text: string
  subject: string
  stage: number
  due_at: string
  knowledge_points: string[]
}

export interface Analytics {
  total_mistakes: number
  new_last_7_days: number
  by_subject: Record<string, number>
  top_knowledge_points: Array<{ name: string; count: number }>
  top_wrong_count: Array<Record<string, unknown>>
  error_reasons: Record<string, number>
  review_completion_rate: number
  repeat_wrong: Array<Record<string, unknown>>
  weak_knowledge_points: Array<Record<string, unknown>>
}

export interface KnowledgePoint {
  id: number
  name: string
  subject: string
  question_count: number
  mistake_count: number
}

export interface SystemStatus {
  app: string
  version: string
  storage_backend: string
  ai_provider: string
  ai_available: boolean
  ocr: Record<string, unknown>
  limits: Record<string, unknown>
  thresholds: { recommend: number; suspect: number }
  review_intervals: number[]
}
