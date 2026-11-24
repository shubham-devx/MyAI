/* eslint-disable @next/next/no-img-element -- attachment previews are transient data URLs. */
'use client'

import { ArrowUp, FileText, ImagePlus, Mic, MicOff, Paperclip, Sparkles, Square, Wrench, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

export type ComposerAttachment = { id: string; name: string; kind: 'image' | 'document'; preview?: string }
type Props = {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  isTyping: boolean
  onStop: () => void
  modelLabel: string
  attachments: ComposerAttachment[]
  uploadStatus: string
  uploadProgress: number
  disableSend: boolean
  onFilesSelected: (files: File[]) => void
  onRemoveAttachment: (attachment: ComposerAttachment) => void
  onOpenTools: () => void
  activeToolCount: number
  onVoiceInput: () => void
  isListening: boolean
  voiceAvailable: boolean
  voiceHint: string
}

export function ChatComposer({ value, onChange, onSend, isTyping, onStop, modelLabel, attachments, uploadStatus, uploadProgress, disableSend, onFilesSelected, onRemoveAttachment, onOpenTools, activeToolCount, onVoiceInput, isListening, voiceAvailable, voiceHint }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!ref.current) return
    ref.current.style.height = 'auto'
    ref.current.style.height = `${Math.min(ref.current.scrollHeight, 160)}px`
  }, [value])

  return <div className="composer-wrap">
    {attachments.length > 0 && <div className="attachment-tray" aria-label="Conversation attachments">{attachments.map((item) => <div className="attachment-chip" key={item.id} title={item.name}>
      {item.preview ? <img src={item.preview} alt={`Preview of ${item.name}`} /> : item.kind === 'image' ? <ImagePlus size={15} /> : <FileText size={15} />}
      <span>{item.name}</span><button onClick={() => onRemoveAttachment(item)} aria-label={`Remove ${item.name}`}><X size={13} /></button>
    </div>)}</div>}
    {uploadStatus && <div className="upload-status" role="status">{uploadStatus}{uploadProgress > 0 && uploadProgress < 100 && <span>{uploadProgress}%</span>}</div>}
    {uploadProgress > 0 && uploadProgress < 100 && <progress className="upload-progress" value={uploadProgress} max={100} aria-label="Document upload progress" />}
    <div className="composer">
      <input ref={fileRef} className="visually-hidden-file" type="file" multiple accept=".pdf,.docx,.txt,.csv,image/png,image/jpeg,image/webp" onChange={(event) => {
        const files = Array.from(event.currentTarget.files ?? [])
        if (files.length) onFilesSelected(files)
        event.currentTarget.value = ''
      }} />
      <button className="attachment-button" onClick={() => fileRef.current?.click()} aria-label="Attach documents or images" title="Attach PDF, DOCX, TXT, CSV, or an image"><Paperclip size={18} /></button>
      <button className={`attachment-button tool-toggle ${activeToolCount ? 'is-active' : ''}`} onClick={onOpenTools} aria-label="Open AI tools" title={activeToolCount ? `${activeToolCount} tool${activeToolCount === 1 ? '' : 's'} enabled` : 'Open AI tools'}>
        <Wrench size={17} />{activeToolCount ? <span className="tool-toggle-count">{activeToolCount}</span> : null}
      </button>
      <textarea ref={ref} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => {
        if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); onSend() }
      }} placeholder="Ask anything..." rows={1} aria-label="Message V-Sutra" />
      <button className={`attachment-button voice-button ${isListening ? 'is-listening' : ''}`} onClick={onVoiceInput} disabled={!voiceAvailable} aria-label={isListening ? 'Stop voice input' : 'Start voice input'} title={voiceHint}>
        {isListening ? <MicOff size={18} /> : <Mic size={18} />}
      </button>
      {isTyping ? <button className="send-button stop" onClick={onStop} aria-label="Stop generating"><Square size={13} fill="currentColor" /></button> : <button className="send-button" onClick={onSend} disabled={disableSend || (!value.trim() && !attachments.some((attachment) => attachment.kind === 'image'))} aria-label="Send message"><ArrowUp size={18} /></button>}
    </div>
    <div className="composer-footer"><span><Sparkles size={13} /><span className="composer-model-label">{modelLabel || 'Select a model'}</span></span><span>{activeToolCount ? `${activeToolCount} tool${activeToolCount === 1 ? '' : 's'} on · ` : ''}Enter to send · Shift + Enter for new line</span></div>
  </div>
}
