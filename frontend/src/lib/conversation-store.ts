import type { Conversation } from '@/lib/types'

export const CONVERSATION_STORAGE_KEY = 'v-sutra-conversations-v1'

export function loadConversations(): Conversation[] {
  let raw: string | null
  try {
    raw = window.localStorage.getItem(CONVERSATION_STORAGE_KEY)
  } catch {
    throw new Error('Browser storage is unavailable. Chats will last only until this page closes.')
  }
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) throw new Error('Saved conversations are not in a supported format.')
    return parsed.filter(isConversation).map((conversation) => {
      const messages = conversation.messages.map((message) => {
        const safeMessage = { ...message }
        // Image bytes are deliberately transient and never restored from localStorage.
        delete safeMessage.imageDataUrls
        return safeMessage
      })
      return { ...conversation, messages, documents: conversation.documents ?? [] }
    })
  } catch {
    throw new Error('Saved conversation history could not be read. Clear temporary history to start again.')
  }
}

export function saveConversations(conversations: Conversation[]): void {
  try {
    const safe = conversations.map((conversation) => ({
      ...conversation,
      messages: conversation.messages.map((message) => {
        const safeMessage = { ...message }
        delete safeMessage.imageDataUrls
        return safeMessage
      }),
    }))
    window.localStorage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify(safe))
  } catch (error) {
    if (error instanceof DOMException && error.name === 'QuotaExceededError') {
      throw new Error('Browser storage is full. Clear temporary history or remove older conversations to keep saving chats.')
    }
    throw new Error('Browser storage is unavailable. Chats may not survive a page refresh.')
  }
}

function isConversation(value: unknown): value is Conversation {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<Conversation>
  return typeof item.id === 'string'
    && typeof item.title === 'string'
    && typeof item.updatedAt === 'string'
    && Array.isArray(item.messages)
    && (!item.documents || Array.isArray(item.documents))
    && item.messages.every((message) => message && typeof message.id === 'string' && (message.role === 'user' || message.role === 'assistant') && typeof message.content === 'string' && typeof message.time === 'string')
}
