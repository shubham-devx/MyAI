/* eslint-disable @next/next/no-img-element -- chat image previews are transient data URLs. */
'use client'

import { AlertTriangle, Check, Copy, Download, ExternalLink, Loader2, RefreshCw, Search, Sparkles, Square, Volume2, Wrench, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import { useEffect, useRef, useState } from 'react'
import type { Message, SearchSource, ToolActivity } from '@/lib/types'
import { copyText, downloadText, messageToMarkdown, safeFilename } from '@/lib/export'

const toolLabels: Record<string, string> = {
  web_search: 'Web search',
  calculator: 'Calculator',
  analyze_csv: 'CSV analysis',
  run_code: 'Sandbox',
}

function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function ToolIcon({ status }: { status: ToolActivity['status'] }) {
  if (status === 'running') return <Loader2 size={13} className="spin" />
  if (status === 'error') return <AlertTriangle size={13} />
  if (status === 'denied') return <XCircle size={13} />
  return <Check size={13} />
}

function ToolActivityRow({ activity }: { activity: ToolActivity }) {
  return <div className={`tool-activity is-${activity.status}`}>
    <span className="tool-activity-icon"><ToolIcon status={activity.status} /></span>
    <div className="tool-activity-copy">
      <strong>{toolLabels[activity.tool] ?? activity.tool}<span>{activity.status === 'running' ? 'working…' : activity.status}</span></strong>
      <small>{activity.summary}</small>
    </div>
    {activity.sources?.length ? <span className="tool-activity-count">{activity.sources.length} source{activity.sources.length === 1 ? '' : 's'}</span> : null}
  </div>
}

function SourceLinks({ sources }: { sources: SearchSource[] }) {
  return <div className="search-sources" aria-label="Web sources">
    <span className="search-sources-title"><Search size={12} /> Web sources</span>
    {sources.map((source, index) => {
      const url = safeHttpUrl(source.url)
      return <span className="search-source" key={`${source.url}-${index}`}>
        {url ? <a href={url} target="_blank" rel="noreferrer noopener">{source.title || url}<ExternalLink size={10} /></a> : <span>{source.title}</span>}
        {source.provider ? <em>{source.provider}</em> : null}
      </span>
    })}
  </div>
}

export function ChatMessage({ message, onRegenerate, onSpeak, onRequireAuth, speaking }: { message: Message; onRegenerate?: () => void; onSpeak?: () => void; onRequireAuth?: () => boolean; speaking?: boolean }) {
  const [copied, setCopied] = useState(false)
  const [actionError, setActionError] = useState('')
  const copyTimer = useRef<number | null>(null)
  useEffect(() => () => {
    if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
  }, [])
  const copy = async () => {
    if (onRequireAuth && !onRequireAuth()) return
    try {
      await copyText(message.content)
      setCopied(true)
      setActionError('')
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
      copyTimer.current = window.setTimeout(() => { setCopied(false); copyTimer.current = null }, 1500)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not copy this response.')
    }
  }
  const exportResponse = () => {
    if (onRequireAuth && !onRequireAuth()) return
    downloadText(`${safeFilename(`v-sutra-${message.time}`)}.md`, messageToMarkdown(message))
  }
  if (message.role === 'user') return <article className="message message-user">
    <div className="user-bubble">{message.content}{message.imageDataUrls?.length ? <div className="message-images">{message.imageDataUrls.map((url, index) => <img src={url} alt={`Attached image ${index + 1}`} key={`${message.id}-image-${index}`} />)}</div> : null}
      {message.attachments?.filter((file) => file.kind === 'document').map((file) => <span className="message-file" key={`${message.id}-${file.name}`}>📄 {file.name}</span>)}
      {message.attachments?.some((file) => file.kind === 'image') && !message.imageDataUrls?.length ? <small className="expired-image-note">Image preview is temporary and was cleared after reload.</small> : null}
    </div><time>{message.time}</time>
  </article>
  return <article className="message message-ai"><div className="assistant-avatar"><Sparkles size={14} /></div><div className="assistant-content">
    <div className="message-meta"><span>V-Sutra</span><time>{message.time}</time>{message.source === 'demo' && <span className="demo-pill">Demo response</span>}</div>
    {message.tools?.length ? <div className="tool-activity-list" aria-label="Tool activity">{message.tools.map((activity) => <ToolActivityRow activity={activity} key={activity.id} />)}</div> : null}
    <div className="markdown-content"><ReactMarkdown>{message.content}</ReactMarkdown></div>
    {message.sources?.length ? <div className="answer-sources">Analyzed documents: {message.sources.join(', ')}</div> : null}
    {message.searchSources?.length ? <SourceLinks sources={message.searchSources} /> : null}
    {actionError ? <div className="message-action-error" role="alert">{actionError}</div> : null}
    <div className="message-actions">
      <button onClick={copy} aria-label="Copy response">{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? 'Copied' : 'Copy'}</button>
      <button onClick={exportResponse} aria-label="Export response as Markdown"><Download size={15} />Export</button>
      {onSpeak ? <button onClick={onSpeak} aria-label={speaking ? 'Stop reading response' : 'Read response aloud'}>{speaking ? <Square size={13} fill="currentColor" /> : <Volume2 size={15} />}{speaking ? 'Stop' : 'Speak'}</button> : null}
      {message.source === 'live' && onRegenerate ? <button onClick={onRegenerate} aria-label="Regenerate response"><RefreshCw size={15} />Regenerate</button> : null}
    </div>
    {message.tools?.some((activity) => activity.tool === 'web_search') ? <small className="external-tool-note"><Wrench size={11} /> This response used an external search tool.</small> : null}
  </div></article>
}
