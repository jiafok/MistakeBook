import type {
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

export interface AiProviderOption {
  value: string
  label: string
  needs_key: string
  needs_base: string
}

export interface AiSettings {
  provider: string
  model: string
  base_url: string
  timeout_seconds: number
  has_api_key: boolean
  api_key_source: 'db' | 'env' | 'none'
  api_key_masked: string
  configured: boolean
  providers: AiProviderOption[]
  default_models: Record<string, string>
  env_fallback: {
    provider: string
    has_api_key: boolean
    base_url: boolean
    model: string
  }
}

export interface AiTestResult {
  ok: boolean
  provider: string
  model: string
  latency_ms: number
  message: string
}

export type {
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
}
