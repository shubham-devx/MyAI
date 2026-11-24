import type {
  ApiChatMessage,
  CsvAnalysis,
  ModelOption,
  ProviderStatus,
  SandboxResult,
  SearchSource,
  StreamDone,
  ToolStreamEvent,
  ToolsStatus,
  UploadedDocument,
  VoiceStatus,
  ConfirmationRequest,
} from '@/lib/types'
import { readSseEvents } from './sse-events.js'

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? (process.env.NODE_ENV === 'production' ? '/backend-api' : 'http://localhost:8000')

type StreamOptions = {
  messages: ApiChatMessage[]
  model: string
  conversationId: string | null
  contextTurnLimit: number
  enabledTools: string[]
  signal: AbortSignal
  onToken: (token: string) => void
  onSources: (sources: string[]) => void
  onToolEvent?: (event: ToolStreamEvent) => void
  onConfirmation?: (request: ConfirmationRequest) => void
  onNotice?: (message: string) => void
  onDone?: (done: StreamDone) => void
}

type ApiError = { detail?: string | { msg?: string }[]; error?: string }

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('vs-auth-required'))
  try {
    const body = await response.json() as ApiError
    const detail = typeof body.detail === 'string' ? body.detail : body.detail?.find((item) => item.msg)?.msg
    return detail ?? body.error ?? 'The AI service could not complete the request.'
  } catch {
    return 'The AI service could not complete the request.'
  }
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, { ...init, credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
  return await response.json() as T
}

export async function getModels(signal?: AbortSignal): Promise<{ models: ModelOption[]; providers: ProviderStatus[]; defaultModel: string | null }> {
  const response = await fetch(`${apiBaseUrl}/api/models`, { signal, credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
  const body = await response.json() as { models: ModelOption[]; providers: ProviderStatus[]; default_model: string | null }
  return { models: body.models, providers: body.providers, defaultModel: body.default_model }
}

export async function uploadDocuments(conversationId: string, files: File[], onProgress: (progress: number) => void): Promise<UploadedDocument[]> {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', `${apiBaseUrl}/api/conversations/${encodeURIComponent(conversationId)}/documents`)
    request.withCredentials = true
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    request.onerror = () => reject(new Error('Could not reach the document service.'))
    request.onload = () => {
      let body: { documents?: UploadedDocument[]; detail?: string }
      try { body = JSON.parse(request.responseText) as typeof body } catch { body = {} }
      if (request.status === 401 && typeof window !== 'undefined') window.dispatchEvent(new Event('vs-auth-required'))
      if (request.status < 200 || request.status >= 300) reject(new Error(body.detail ?? 'The document could not be processed.'))
      else resolve(body.documents ?? [])
    }
    request.send(form)
  })
}

export async function listDocuments(conversationId: string, signal?: AbortSignal): Promise<UploadedDocument[]> {
  const response = await fetch(`${apiBaseUrl}/api/conversations/${encodeURIComponent(conversationId)}/documents`, { signal, credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
  const body = await response.json() as { documents: UploadedDocument[] }
  return body.documents
}

export async function removeDocument(conversationId: string, documentId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/conversations/${encodeURIComponent(conversationId)}/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE', credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
}

export async function clearConversationDocuments(conversationId: string): Promise<void> {
  await fetch(`${apiBaseUrl}/api/conversations/${encodeURIComponent(conversationId)}/documents`, { method: 'DELETE', credentials: 'include' })
}

export async function clearAllTemporaryDocuments(): Promise<void> {
  await fetch(`${apiBaseUrl}/api/documents/temporary`, { method: 'DELETE', credentials: 'include' })
}

export async function streamChat({ messages, model, conversationId, contextTurnLimit, enabledTools, signal, onToken, onSources, onToolEvent, onConfirmation, onNotice, onDone }: StreamOptions): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/chat/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, model, conversation_id: conversationId, context_turn_limit: contextTurnLimit, enabled_tools: enabledTools }),
    signal,
  })

  if (!response.ok || !response.body) throw new Error(await errorMessage(response))

  let receivedDone = false
  for await (const raw of readSseEvents(response.body)) {
    let payload: Record<string, unknown>
    try { payload = JSON.parse(raw) as Record<string, unknown> } catch { throw new Error('The model service sent a malformed streaming event.') }
    const type = payload.type
    if (type === 'error') throw new Error(typeof payload.error === 'string' ? payload.error : 'The model failed while generating a response.')
    if (type === 'token' && typeof payload.token === 'string') onToken(payload.token)
    if (type === 'notice' && typeof payload.message === 'string') onNotice?.(payload.message)
    if (type === 'tool_request' && typeof payload.call_id === 'string' && typeof payload.tool === 'string') {
      onToolEvent?.({ kind: 'request', callId: payload.call_id, tool: payload.tool, arguments: isRecord(payload.arguments) ? payload.arguments : {} })
    }
    if (type === 'tool_confirmation' && typeof payload.confirmation_id === 'string' && typeof payload.tool === 'string') {
      onConfirmation?.({
        confirmationId: payload.confirmation_id,
        callId: typeof payload.call_id === 'string' ? payload.call_id : '',
        tool: payload.tool,
        arguments: isRecord(payload.arguments) ? payload.arguments : {},
        expiresIn: typeof payload.expires_in === 'number' ? payload.expires_in : 120,
      })
    }
    if (type === 'tool_result' && typeof payload.call_id === 'string' && typeof payload.tool === 'string') {
      const status = payload.status === 'error' || payload.status === 'denied' ? payload.status : payload.status === 'running' ? 'running' : 'ok'
      onToolEvent?.({
        kind: 'result', callId: payload.call_id, tool: payload.tool, status,
        summary: typeof payload.summary === 'string' ? payload.summary : 'Tool completed.',
        data: payload.data, sources: Array.isArray(payload.sources) ? payload.sources.filter(isSource) : [],
      })
    }
    if (type === 'done') {
      receivedDone = true
      const sources = Array.isArray(payload.sources) ? payload.sources.filter((item): item is string => typeof item === 'string') : []
      const searchSources = Array.isArray(payload.search_sources) ? payload.search_sources.filter(isSource) : []
      const toolsUsed = Array.isArray(payload.tools_used) ? payload.tools_used.filter(isToolUse) : []
      onSources(sources)
      onDone?.({ sources, searchSources, toolsUsed })
    }
  }
  if (!receivedDone) throw new Error('The model stream ended before completion. Try again.')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isSource(value: unknown): value is SearchSource {
  return isRecord(value) && typeof value.url === 'string' && typeof value.title === 'string'
}

function isToolUse(value: unknown): value is { name: string; status: string; summary: string } {
  return isRecord(value) && typeof value.name === 'string' && typeof value.status === 'string' && typeof value.summary === 'string'
}

export async function getToolsStatus(signal?: AbortSignal): Promise<ToolsStatus> {
  return apiJson<ToolsStatus>('/api/tools', { signal })
}

export async function calculateExpression(expression: string): Promise<{ expression: string; result: string; value: number }> {
  return apiJson('/api/tools/calculator', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expression }),
  })
}

export async function searchWeb(query: string, maxResults = 5): Promise<{ query: string; results: SearchSource[] }> {
  return apiJson('/api/tools/web-search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, max_results: maxResults }),
  })
}

export async function analyzeCsvFile(file: File): Promise<{ name: string; analysis: CsvAnalysis; summary: string }> {
  const form = new FormData()
  form.append('file', file)
  return apiJson('/api/tools/csv/analyze', { method: 'POST', body: form })
}

export async function generateCode(payload: { prompt: string; language: string; model: string; requirements?: string }): Promise<{ code: string; explanation: string; language: string; model: string; provider: string }> {
  return apiJson('/api/tools/code/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function runSandbox(payload: { language: 'python' | 'javascript'; code: string; stdin?: string; confirmed: boolean }): Promise<SandboxResult> {
  return apiJson('/api/tools/sandbox/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function generateStructured(payload: { prompt: string; schema: Record<string, unknown>; model: string; system?: string }): Promise<{ json: unknown; raw: string; schema: Record<string, unknown>; attempts: number; model: string; provider: string }> {
  return apiJson('/api/tools/structured', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function resolveToolConfirmation(confirmationId: string, approved: boolean): Promise<void> {
  await apiJson(`/api/tool-confirmations/${encodeURIComponent(confirmationId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved }),
  })
}

export async function getVoiceStatus(signal?: AbortSignal): Promise<VoiceStatus> {
  return apiJson<VoiceStatus>('/api/voice/status', { signal })
}

export async function transcribeAudio(blob: Blob): Promise<{ text: string; provider: string; model: string }> {
  const form = new FormData()
  const extension = blob.type.includes('wav') ? 'wav' : blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'
  form.append('file', blob, `recording.${extension}`)
  return apiJson('/api/voice/transcribe', { method: 'POST', body: form })
}

export async function synthesizeSpeech(text: string): Promise<Blob> {
  const response = await fetch(`${apiBaseUrl}/api/voice/speech`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!response.ok) throw new Error(await errorMessage(response))
  return await response.blob()
}
