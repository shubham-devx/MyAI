export type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  time: string
  source: 'demo' | 'live'
  attachments?: MessageAttachment[]
  imageDataUrls?: string[]
  sources?: string[]
  searchSources?: SearchSource[]
  tools?: ToolActivity[]
}

export type Conversation = {
  id: string
  title: string
  updatedAt: string
  messages: Message[]
  documents: UploadedDocument[]
}

export type MessageAttachment = { name: string; kind: 'image' | 'document' }

export type UploadedDocument = {
  id: string
  name: string
  type: string
  size: number
  chunks: number
  rows?: number
  columns?: number
  truncated?: boolean
}

export type ApiChatMessage = { role: 'system' | 'user' | 'assistant'; content: string; images?: string[] }

export type ModelOption = {
  id: string
  provider: string
  name: string
  context_window?: number | null
  capabilities: string[]
  available: boolean
  configuration_required?: boolean
  size?: number
  modified_at?: string
}

export type ProviderStatus = { id: string; name: string; configured: boolean; available: boolean; configuration_hint?: string | null }

export type SearchSource = { title: string; url: string; snippet?: string; provider?: string }

export type ToolActivityStatus = 'running' | 'ok' | 'error' | 'denied'

export type ToolActivity = {
  id: string
  tool: string
  label?: string
  status: ToolActivityStatus
  summary: string
  arguments?: Record<string, unknown>
  sources?: SearchSource[]
  data?: unknown
}

export type ToolStatus = {
  name: string
  label: string
  description: string
  external: boolean
  requires_confirmation: boolean
  available: boolean
  reason?: string | null
}

export type SearchProviderStatus = {
  provider: string
  label: string
  configured: boolean
  external: boolean
  hint?: string | null
}

export type SandboxLanguageStatus = { name: string; label: string; image: string; ready: boolean }

export type SandboxStatus = {
  enabled: boolean
  engine: string | null
  available: boolean
  hint?: string | null
  languages: SandboxLanguageStatus[]
  limits: {
    timeout_seconds: number
    memory_mb: number
    cpus: number
    pids: number
    network: string
    filesystem: string
    output_bytes: number
  }
}

export type ToolsStatus = {
  tools: ToolStatus[]
  search: SearchProviderStatus
  sandbox: SandboxStatus
  tool_calling: { streaming: boolean; confirmation: boolean }
}

export type VoiceEngineStatus = {
  provider: string
  label: string
  configured: boolean
  available: boolean
  base_url?: string | null
  model?: string | null
  voice?: string | null
  format?: string
  hint?: string | null
}

export type VoiceStatus = {
  stt: VoiceEngineStatus
  tts: VoiceEngineStatus
  limits: { max_upload_mb: number; max_text_chars: number }
}

export type ConfirmationRequest = {
  confirmationId: string
  callId: string
  tool: string
  arguments: Record<string, unknown>
  expiresIn: number
}

export type ToolStreamEvent =
  | { kind: 'request'; callId: string; tool: string; arguments: Record<string, unknown> }
  | { kind: 'result'; callId: string; tool: string; status: ToolActivityStatus; summary: string; data?: unknown; sources?: SearchSource[] }

export type StreamDone = {
  sources: string[]
  searchSources: SearchSource[]
  toolsUsed: { name: string; status: string; summary: string }[]
}

export type CsvAnalysis = {
  name: string
  row_count: number
  column_count: number
  truncated: boolean
  delimiter: string
  columns: {
    name: string
    type: string
    missing: number
    missing_ratio: number
    unique: number
    numeric?: Record<string, number>
    numeric_ratio?: number
    top_values?: { value: string; count: number }[]
  }[]
  correlations: { left: string; right: string; r: number; strength: string }[]
  preview: Record<string, string>[]
  insights: string[]
}

export type SandboxResult = {
  ok: boolean
  language: string
  exit_code: number | null
  stdout: string
  stderr: string
  duration_ms: number
  timed_out: boolean
  truncated: boolean
  engine: string
  image: string
}
