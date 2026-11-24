import type { Conversation, Message } from '@/lib/types'

export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    return
  } catch {
    // Clipboard API needs a secure context; fall back for local HTTP/older browsers.
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  document.body.removeChild(textarea)
  if (!copied) throw new Error('The browser could not copy to the clipboard.')
}

export function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function safeFilename(value: string): string {
  const cleaned = value.trim().replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 70)
  return cleaned || 'v-sutra-export'
}

export function messageToMarkdown(message: Message): string {
  const lines = [`## ${message.role === 'assistant' ? 'V-Sutra' : 'You'} · ${message.time}`, '', message.content]
  if (message.sources?.length) lines.push('', `**Analyzed documents:** ${message.sources.join(', ')}`)
  if (message.searchSources?.length) {
    lines.push('', '**Web sources:**')
    message.searchSources.forEach((source) => lines.push(`- [${source.title}](${source.url})`))
  }
  if (message.tools?.length) {
    lines.push('', '**Tool activity:**')
    message.tools.forEach((activity) => lines.push(`- ${activity.tool} — ${activity.status}: ${activity.summary}`))
  }
  return lines.join('\n')
}

export function conversationToMarkdown(conversation: Conversation): string {
  const header = [`# ${conversation.title}`, '', `Exported from V-Sutra · ${new Date().toLocaleString()}`, '']
  const body = conversation.messages.map(messageToMarkdown).join('\n\n---\n\n')
  return [...header, body].join('\n')
}
