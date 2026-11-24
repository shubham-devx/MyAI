'use client'

import { Download, LogIn, LogOut, Menu, Plus } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  clearAllTemporaryDocuments,
  clearConversationDocuments,
  getModels,
  getToolsStatus,
  getVoiceStatus,
  listDocuments,
  removeDocument,
  resolveToolConfirmation,
  streamChat,
  synthesizeSpeech,
  transcribeAudio,
  uploadDocuments,
} from '@/lib/chat-stream'
import { CONVERSATION_STORAGE_KEY, loadConversations, saveConversations } from '@/lib/conversation-store'
import { getCurrentUser, signOut, type AuthUser } from '@/lib/auth-client'
import { conversationToMarkdown, downloadText, safeFilename } from '@/lib/export'
import { browserRecognitionSupported, browserSynthesisSupported, browserVoices, canRecordAudio, createBrowserRecognition, speakWithBrowser, stopBrowserSpeech, type BrowserRecognition } from '@/lib/speech'
import type { ApiChatMessage, ConfirmationRequest, Conversation, Message, ModelOption, ProviderStatus, StreamDone, ToolStreamEvent, ToolsStatus, VoiceStatus } from '@/lib/types'
import { ChatComposer, type ComposerAttachment } from './ChatComposer'
import { AuthDialog } from './AuthDialog'
import { ChatMessage } from './ChatMessage'
import { SettingsPanel, type ContextTurnLimit, type ResponsePreference, type TextSizePreference, type ThemePreference } from './SettingsPanel'
import { Sidebar } from './Sidebar'
import { ToolConfirmation } from './ToolConfirmation'
import { ToolsPanel } from './ToolsPanel'
import { WelcomeScreen } from './WelcomeScreen'

const createConversation = (): Conversation => ({ id: crypto.randomUUID(), title: 'New conversation', updatedAt: new Date().toISOString(), messages: [], documents: [] })
const fileExtension = (name: string) => name.slice(name.lastIndexOf('.')).toLowerCase()
const knownTools = ['web_search', 'calculator', 'analyze_csv', 'run_code']

const plainTextForSpeech = (text: string) => text
  .replace(/```[\s\S]*?```/g, ' code block ')
  .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
  .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
  .replace(/[#>*_`~|]/g, ' ')
  .replace(/\s+/g, ' ')
  .trim()
  .slice(0, 4000)

type SavedPreferences = {
  theme?: ThemePreference
  textSize?: TextSizePreference
  responseStyle?: ResponsePreference
  contextTurnLimit?: ContextTurnLimit
  enabledTools?: string[]
  speakReplies?: boolean
  voiceRate?: number
  voiceName?: string
}

export function NexaApp() {
  const [collapsed, setCollapsed] = useState(false)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState('')
  const [input, setInput] = useState('')
  const [pendingImages, setPendingImages] = useState<{ id: string; name: string; dataUrl: string }[]>([])
  const [typing, setTyping] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showTools, setShowTools] = useState(false)
  const [authOpen, setAuthOpen] = useState(false)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [passwordResetEmailEnabled, setPasswordResetEmailEnabled] = useState(false)
  const [resetToken, setResetToken] = useState('')
  const [queuedFiles, setQueuedFiles] = useState<File[]>([])
  const [model, setModel] = useState('')
  const [models, setModels] = useState<ModelOption[]>([])
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [modelStatus, setModelStatus] = useState('Loading model providers…')
  const [storageStatus, setStorageStatus] = useState('')
  const [storageReady, setStorageReady] = useState(false)
  const [storageWritable, setStorageWritable] = useState(true)
  const [uploadStatus, setUploadStatus] = useState('')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [documentStatus, setDocumentStatus] = useState('')
  const [theme, setTheme] = useState<ThemePreference>('system')
  const [textSize, setTextSize] = useState<TextSizePreference>('default')
  const [responseStyle, setResponseStyle] = useState<ResponsePreference>('balanced')
  const [contextTurnLimit, setContextTurnLimit] = useState<ContextTurnLimit>(8)
  const [prefersLight, setPrefersLight] = useState(false)
  const [preferencesReady, setPreferencesReady] = useState(false)
  const [enabledTools, setEnabledTools] = useState<string[]>([])
  const [toolsStatus, setToolsStatus] = useState<ToolsStatus | null>(null)
  const [toolsStatusError, setToolsStatusError] = useState('')
  const [toolNotice, setToolNotice] = useState('')
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null)
  const [confirmationPending, setConfirmationPending] = useState(false)
  const [confirmationError, setConfirmationError] = useState('')
  const [speakReplies, setSpeakReplies] = useState(false)
  const [voiceRate, setVoiceRate] = useState(1)
  const [voiceName, setVoiceName] = useState('')
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  const [voiceStatusError, setVoiceStatusError] = useState('')
  const [voiceNames, setVoiceNames] = useState<{ name: string; lang: string }[]>([])
  const [browserSttAvailable, setBrowserSttAvailable] = useState(false)
  const [micAvailable, setMicAvailable] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [speakingId, setSpeakingId] = useState('')
  const abortControllerRef = useRef<AbortController | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const recordedChunksRef = useRef<Blob[]>([])
  const recordingTimerRef = useRef<number | null>(null)
  const recognitionRef = useRef<BrowserRecognition | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioUrlRef = useRef('')

  const activeConversation = conversations.find((conversation) => conversation.id === activeId)
  const messages = activeConversation?.messages ?? []
  const documents = activeConversation?.documents ?? []
  const selectedModel = models.find((item) => item.id === model)
  const supportsVision = selectedModel?.provider === 'ollama' && selectedModel.capabilities.includes('vision')
  const modelLabel = selectedModel ? `${selectedModel.provider} · ${selectedModel.name}` : 'No model selected'
  const resolvedTheme = theme === 'system' ? (prefersLight ? 'theme-light' : '') : (theme === 'light' ? 'theme-light' : '')
  const voiceAvailable = Boolean(voiceStatus?.stt.available) || browserSttAvailable
  const voiceHint = voiceStatus?.stt.available
    ? `Record with ${voiceStatus.stt.label}`
    : browserSttAvailable
      ? 'Record with browser speech recognition'
      : 'Voice input is unavailable. Configure a speech engine on the backend or use a supported browser.'

  const updateConversation = useCallback((id: string, update: (conversation: Conversation) => Conversation) => {
    setConversations((current) => current.map((conversation) => conversation.id === id ? update(conversation) : conversation))
  }, [])

  useEffect(() => {
    try {
      let saved: SavedPreferences = {}
      const raw = localStorage.getItem('v-sutra-preferences')
      if (raw) saved = JSON.parse(raw) as SavedPreferences
      // Initial client-only preference hydration is required after SSR.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (saved.theme && ['dark', 'light', 'system'].includes(saved.theme)) setTheme(saved.theme)
      if (saved.textSize && ['small', 'default', 'large'].includes(saved.textSize)) setTextSize(saved.textSize)
      if (saved.responseStyle && ['concise', 'balanced', 'detailed'].includes(saved.responseStyle)) setResponseStyle(saved.responseStyle)
      if (saved.contextTurnLimit && [2, 4, 8, 12, 20].includes(saved.contextTurnLimit)) setContextTurnLimit(saved.contextTurnLimit)
      if (Array.isArray(saved.enabledTools)) setEnabledTools(saved.enabledTools.filter((name): name is string => typeof name === 'string' && knownTools.includes(name)))
      if (typeof saved.speakReplies === 'boolean') setSpeakReplies(saved.speakReplies)
      if (typeof saved.voiceRate === 'number' && [0.85, 1, 1.15, 1.3].includes(saved.voiceRate)) setVoiceRate(saved.voiceRate)
      if (typeof saved.voiceName === 'string') setVoiceName(saved.voiceName)
    } catch { /* Workspace preferences are optional and have safe defaults. */ }
    setPreferencesReady(true)
    const themeMedia = window.matchMedia('(prefers-color-scheme: light)')
    const updateTheme = () => setPrefersLight(themeMedia.matches)
    updateTheme()
    themeMedia.addEventListener('change', updateTheme)
    setBrowserSttAvailable(browserRecognitionSupported())
    setMicAvailable(canRecordAudio())
    return () => themeMedia.removeEventListener('change', updateTheme)
  }, [])

  useEffect(() => {
    if (!preferencesReady) return
    try {
      localStorage.setItem('v-sutra-preferences', JSON.stringify({ theme, textSize, responseStyle, contextTurnLimit, enabledTools, speakReplies, voiceRate, voiceName }))
    } catch { /* Current-session preferences still apply. */ }
  }, [preferencesReady, theme, textSize, responseStyle, contextTurnLimit, enabledTools, speakReplies, voiceRate, voiceName])

  useEffect(() => {
    try {
      const stored = loadConversations()
      const initial = stored.length ? stored.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt)) : [createConversation()]
      // Initial client-only transcript hydration is required after SSR.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setConversations(initial)
      setActiveId(initial[0].id)
      setStorageStatus('')
    } catch (error) {
      const temporary = createConversation()
      setConversations([temporary])
      setActiveId(temporary.id)
      setStorageWritable(false)
      setStorageStatus(error instanceof Error ? error.message : 'Browser storage is unavailable. Chats are temporary for this session.')
    }
    setStorageReady(true)
  }, [])

  useEffect(() => {
    if (!storageReady || !storageWritable) return
    const persist = () => {
      try { saveConversations(conversations); setStorageStatus('') } catch (error) {
        setStorageWritable(false)
        setStorageStatus(error instanceof Error ? error.message : 'Could not save browser conversation history.')
      }
    }
    const timer = window.setTimeout(persist, 350)
    const flush = () => persist()
    window.addEventListener('pagehide', flush)
    return () => { window.clearTimeout(timer); window.removeEventListener('pagehide', flush) }
  }, [conversations, storageReady, storageWritable])

  useEffect(() => {
    const mobileMedia = window.matchMedia('(max-width: 720px)')
    const updateNavigation = () => setCollapsed(mobileMedia.matches)
    updateNavigation()
    mobileMedia.addEventListener('change', updateNavigation)
    return () => mobileMedia.removeEventListener('change', updateNavigation)
  }, [])

  useEffect(() => {
    getCurrentUser().then(({ user, password_reset_email_enabled }) => { setAuthUser(user); setPasswordResetEmailEnabled(password_reset_email_enabled) }).catch(() => setAuthUser(null))
    const token = new URLSearchParams(window.location.search).get('reset')
    Promise.resolve().then(() => { if (token) { setResetToken(token); setAuthOpen(true) } })
  }, [])

  useEffect(() => {
    if (!authUser) return
    const controller = new AbortController()
    getVoiceStatus(controller.signal).then((status) => { setVoiceStatus(status); setVoiceStatusError('') }).catch((error) => {
      if ((error as DOMException).name !== 'AbortError') setVoiceStatusError(error instanceof Error ? error.message : 'Voice engine status is unavailable.')
    })
    return () => controller.abort()
  }, [authUser])

  useEffect(() => {
    const controller = new AbortController()
    getModels(controller.signal)
      .then(({ models: catalog, providers: providerCatalog, defaultModel }) => {
        setModels(catalog)
        setProviders(providerCatalog)
        setModel(defaultModel ?? '')
        const availableCount = catalog.filter((item) => item.available).length
        const readyProviders = providerCatalog.filter((item) => item.available).map((item) => item.name)
        const setupProviders = providerCatalog.filter((item) => !item.available).map((item) => item.name)
        const availability = availableCount ? `${availableCount} models available · ${readyProviders.join(', ')}` : 'No models available'
        setModelStatus(`${availability}${setupProviders.length ? ` · Configure: ${setupProviders.join(', ')}` : ''}`)
      })
      .catch((error) => setModelStatus(error instanceof Error ? error.message : 'Model providers unavailable'))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const showSignIn = () => { setAuthUser(null); setAuthOpen(true) }
    window.addEventListener('vs-auth-required', showSignIn)
    return () => window.removeEventListener('vs-auth-required', showSignIn)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getToolsStatus(controller.signal).then((status) => { setToolsStatus(status); setToolsStatusError('') }).catch((error) => {
      if ((error as DOMException).name !== 'AbortError') setToolsStatusError(error instanceof Error ? error.message : 'Tool status is unavailable.')
    })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!browserSynthesisSupported()) return
    const loadVoices = () => setVoiceNames(browserVoices().map((voice) => ({ name: voice.name, lang: voice.lang })))
    loadVoices()
    window.speechSynthesis.addEventListener('voiceschanged', loadVoices)
    return () => window.speechSynthesis.removeEventListener('voiceschanged', loadVoices)
  }, [])

  useEffect(() => {
    if (!toolNotice) return
    const timer = window.setTimeout(() => setToolNotice(''), 9000)
    return () => window.clearTimeout(timer)
  }, [toolNotice])

  const savedDocCount = conversations.find((conversation) => conversation.id === activeId)?.documents.length ?? 0
  useEffect(() => {
    if (!authUser || !storageReady || !activeId) return
    const controller = new AbortController()
    listDocuments(activeId, controller.signal).then((serverDocuments) => {
      updateConversation(activeId, (conversation) => ({ ...conversation, documents: serverDocuments }))
      setDocumentStatus(savedDocCount > 0 && serverDocuments.length === 0 ? 'Temporary document contents expired or the backend restarted. Re-upload files to ask about them again.' : '')
    }).catch(() => setDocumentStatus(savedDocCount > 0 ? 'Could not reconnect to temporary document processing. Re-upload files if needed.' : ''))
    return () => controller.abort()
  }, [activeId, savedDocCount, storageReady, updateConversation, authUser])

  const stopSpeaking = useCallback(() => {
    stopBrowserSpeech()
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = ''
    }
    setSpeakingId('')
  }, [])

  const speakResponse = useCallback(async (messageId: string, text: string) => {
    if (!authUser) { setAuthOpen(true); return }
    if (speakingId === messageId) { stopSpeaking(); return }
    stopSpeaking()
    const spoken = plainTextForSpeech(text)
    if (!spoken) return
    if (voiceStatus?.tts.available && voiceStatus.tts.provider !== 'browser') {
      try {
        setSpeakingId(messageId)
        const blob = await synthesizeSpeech(spoken)
        const url = URL.createObjectURL(blob)
        audioUrlRef.current = url
        const audio = new Audio(url)
        audioRef.current = audio
        audio.onended = () => { setSpeakingId(''); URL.revokeObjectURL(url); audioUrlRef.current = '' }
        audio.onerror = () => { setSpeakingId(''); setToolNotice('The speech engine returned audio that could not be played.'); URL.revokeObjectURL(url); audioUrlRef.current = '' }
        await audio.play()
        return
      } catch (error) {
        setSpeakingId('')
        setToolNotice(error instanceof Error ? error.message : 'Speech output failed.')
      }
    }
    if (!browserSynthesisSupported()) { setToolNotice('No speech engine is available for this response.'); return }
    try {
      setSpeakingId(messageId)
      await speakWithBrowser(spoken, voiceRate, voiceName)
    } catch (error) {
      if (!(error instanceof Error && error.message === 'Speech was stopped.')) setToolNotice(error instanceof Error ? error.message : 'Speech output failed.')
    } finally {
      setSpeakingId('')
    }
  }, [speakingId, stopSpeaking, voiceStatus, voiceRate, voiceName, authUser])

  const stopVoiceInput = useCallback(() => {
    if (recordingTimerRef.current) { window.clearTimeout(recordingTimerRef.current); recordingTimerRef.current = null }
    if (recognitionRef.current) {
      try { recognitionRef.current.stop() } catch { /* Recognition may already be stopped. */ }
      recognitionRef.current = null
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    setIsListening(false)
  }, [])

  const startVoiceInput = async () => {
    if (!authUser) { setAuthOpen(true); return }
    if (isListening) { stopVoiceInput(); return }
    if (voiceBusy) return
    setToolNotice('')
    if (voiceStatus?.stt.available) {
      if (!micAvailable) { setToolNotice('Microphone recording is not available in this browser or requires HTTPS/localhost.'); return }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        mediaStreamRef.current = stream
        const preferredType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/wav'].find((type) => MediaRecorder.isTypeSupported(type)) ?? ''
        const recorder = new MediaRecorder(stream, preferredType ? { mimeType: preferredType } : undefined)
        recordedChunksRef.current = []
        recorder.ondataavailable = (event) => { if (event.data.size) recordedChunksRef.current.push(event.data) }
        recorder.onstop = () => {
          const blob = new Blob(recordedChunksRef.current, { type: recorder.mimeType || 'audio/webm' })
          recordedChunksRef.current = []
          mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
          mediaStreamRef.current = null
          setIsListening(false)
          if (!blob.size) { setVoiceBusy(false); return }
          setVoiceBusy(true)
          void transcribeAudio(blob)
            .then((result) => {
              if (result.text.trim()) setInput((current) => current ? `${current} ${result.text.trim()}` : result.text.trim())
              setToolNotice(`Transcribed with ${result.provider}.`)
            })
            .catch((error) => setToolNotice(error instanceof Error ? error.message : 'Transcription failed.'))
            .finally(() => setVoiceBusy(false))
        }
        mediaRecorderRef.current = recorder
        recorder.start()
        setIsListening(true)
        recordingTimerRef.current = window.setTimeout(() => stopVoiceInput(), 60000)
      } catch {
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
        mediaStreamRef.current = null
        setToolNotice('Microphone permission was denied or no microphone is available.')
      }
      return
    }
    if (browserSttAvailable) {
      const recognition = createBrowserRecognition()
      if (!recognition) { setToolNotice('Browser speech recognition is unavailable.'); return }
      recognitionRef.current = recognition
      recognition.onresult = (event) => {
        let transcript = ''
        for (let index = 0; index < event.results.length; index += 1) {
          transcript += event.results[index]?.[0]?.transcript ?? ''
        }
        if (transcript.trim()) setInput((current) => current ? `${current} ${transcript.trim()}` : transcript.trim())
      }
      recognition.onerror = (event) => {
        setToolNotice(event.error === 'not-allowed' ? 'Microphone permission was denied.' : 'The browser could not transcribe the recording.')
        setIsListening(false)
        recognitionRef.current = null
      }
      recognition.onend = () => {
        setIsListening(false)
        recognitionRef.current = null
      }
      try {
        recognition.start()
        setIsListening(true)
        recordingTimerRef.current = window.setTimeout(() => stopVoiceInput(), 60000)
      } catch {
        setToolNotice('Browser speech recognition could not start.')
        recognitionRef.current = null
      }
      return
    }
    setToolNotice(voiceHint)
  }

  useEffect(() => () => {
    stopBrowserSpeech()
    if (audioRef.current) audioRef.current.pause()
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current)
    if (recordingTimerRef.current) window.clearTimeout(recordingTimerRef.current)
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') mediaRecorderRef.current.stop()
    recognitionRef.current?.abort()
  }, [])

  const openSettings = () => { if (!authUser) { setAuthOpen(true); return } setShowSettings(true) }
  const openTools = () => { if (!authUser) { setAuthOpen(true); return } setShowTools(true) }
  const closeAuth = useCallback(() => { setAuthOpen(false); setQueuedFiles([]) }, [])
  const closeSettings = useCallback(() => setShowSettings(false), [])
  const closeTools = useCallback(() => setShowTools(false), [])
  const timestamp = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const nowIso = () => new Date().toISOString()

  const resetTransient = () => {
    setConfirmation(null)
    setConfirmationError('')
    setConfirmationPending(false)
  }

  const newChat = () => {
    abortControllerRef.current?.abort()
    resetTransient()
    stopSpeaking()
    const conversation = createConversation()
    setConversations((current) => [conversation, ...current])
    setActiveId(conversation.id)
    setInput('')
    setPendingImages([])
    setUploadStatus('')
    setDocumentStatus('')
    if (window.matchMedia('(max-width: 720px)').matches) setCollapsed(true)
  }

  const selectConversation = (id: string) => {
    abortControllerRef.current?.abort()
    resetTransient()
    stopSpeaking()
    setActiveId(id)
    setInput('')
    setPendingImages([])
    setUploadStatus('')
    setDocumentStatus('')
    if (window.matchMedia('(max-width: 720px)').matches) setCollapsed(true)
  }

  const renameConversation = (id: string, title: string) => updateConversation(id, (conversation) => ({ ...conversation, title, updatedAt: nowIso() }))

  const deleteConversation = (id: string) => {
    void clearConversationDocuments(id).catch(() => undefined)
    const remaining = conversations.filter((conversation) => conversation.id !== id)
    if (id === activeId) {
      const next = remaining[0] ?? createConversation()
      if (!remaining.length) remaining.push(next)
      setActiveId(next.id)
      setPendingImages([]); setInput(''); setDocumentStatus('')
    }
    setConversations(remaining)
  }

  const clearCurrentConversation = () => {
    if (!activeId) return
    abortControllerRef.current?.abort()
    resetTransient()
    void clearConversationDocuments(activeId).catch(() => undefined)
    updateConversation(activeId, (conversation) => ({ ...conversation, title: 'New conversation', messages: [], documents: [], updatedAt: nowIso() }))
    setPendingImages([])
    setInput('')
    setDocumentStatus('')
  }

  const clearAllHistory = () => {
    abortControllerRef.current?.abort()
    resetTransient()
    void clearAllTemporaryDocuments().catch(() => setDocumentStatus('Could not clear temporary document processing.'))
    const fresh = createConversation()
    setConversations([fresh])
    setActiveId(fresh.id)
    setPendingImages([])
    setInput('')
    setShowSettings(false)
    try {
      localStorage.removeItem(CONVERSATION_STORAGE_KEY)
      setStorageWritable(true)
      setStorageStatus('')
    } catch {
      setStorageWritable(false)
      setStorageStatus('Could not access browser storage to clear saved conversations. Clear this site’s storage in browser settings.')
    }
  }

  const handleFiles = async (files: File[], authorized = Boolean(authUser)) => {
    if (!authorized) { setQueuedFiles(files); setAuthOpen(true); return }
    if (!activeId) return
    const acceptedDocuments: File[] = []
    const acceptedImages: File[] = []
    const errors: string[] = []
    for (const file of files) {
      const extension = fileExtension(file.name)
      if (file.type.startsWith('image/')) {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) errors.push(`${file.name}: use PNG, JPEG, or WebP.`)
        else if (file.size > 8 * 1024 * 1024) errors.push(`${file.name}: image limit is 8 MB.`)
        else if (!supportsVision) errors.push(`${file.name}: select an Ollama model marked with vision capability first. Text-only models cannot analyze images.`)
        else acceptedImages.push(file)
      } else if (['.pdf', '.docx', '.txt', '.csv'].includes(extension)) {
        if (file.size > 15 * 1024 * 1024) errors.push(`${file.name}: document limit is 15 MB.`)
        else acceptedDocuments.push(file)
      } else errors.push(`${file.name}: unsupported. Use PDF, DOCX, TXT, CSV, PNG, JPEG, or WebP.`)
    }
    setUploadStatus(errors.join(' '))
    setUploadProgress(0)

    if (acceptedImages.length) {
      if (pendingImages.length + acceptedImages.length > 4) errors.push('Attach up to 4 images per message.')
      else {
        for (const file of acceptedImages) {
          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader()
            reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error(`Could not preview ${file.name}.`))
            reader.onerror = () => reject(new Error(`Could not read ${file.name}.`))
            reader.readAsDataURL(file)
          }).catch((error) => { errors.push(error instanceof Error ? error.message : 'Could not read image.'); return '' })
          if (dataUrl) setPendingImages((current) => [...current, { id: crypto.randomUUID(), name: file.name, dataUrl }])
        }
      }
    }
    if (acceptedDocuments.length) {
      setUploadStatus(`Uploading and extracting ${acceptedDocuments.length} document${acceptedDocuments.length === 1 ? '' : 's'}…`)
      try {
        const uploaded = await uploadDocuments(activeId, acceptedDocuments, setUploadProgress)
        setUploadProgress(100)
        updateConversation(activeId, (conversation) => ({ ...conversation, documents: uploaded, updatedAt: nowIso() }))
        setDocumentStatus(`${uploaded.length} file${uploaded.length === 1 ? '' : 's'} ready for temporary retrieval.`)
      } catch (error) {
        setUploadProgress(0)
        errors.push(error instanceof Error ? error.message : 'Document upload failed.')
      }
    }
    setUploadStatus(errors.join(' '))
  }

  const composerAttachments: ComposerAttachment[] = [
    ...documents.map((document) => ({ id: document.id, name: document.name, kind: 'document' as const })),
    ...pendingImages.map((image) => ({ id: image.id, name: image.name, kind: 'image' as const, preview: image.dataUrl })),
  ]

  const removeAttachment = (attachment: ComposerAttachment) => {
    if (attachment.kind === 'image') {
      setPendingImages((current) => current.filter((image) => image.id !== attachment.id))
      return
    }
    void removeDocument(activeId, attachment.id).catch((error) => setDocumentStatus(error instanceof Error ? error.message : 'Could not remove temporary document.'))
    updateConversation(activeId, (conversation) => ({ ...conversation, documents: conversation.documents.filter((document) => document.id !== attachment.id) }))
  }

  const updateToolActivity = (conversationId: string, assistantId: string, event: ToolStreamEvent) => {
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      messages: conversation.messages.map((message) => {
        if (message.id !== assistantId) return message
        const tools = message.tools ? [...message.tools] : []
        if (event.kind === 'request') {
          if (!tools.some((activity) => activity.id === event.callId)) {
            tools.push({ id: event.callId, tool: event.tool, status: 'running', summary: 'Requested by the model', arguments: event.arguments })
          }
        } else {
          const index = tools.findIndex((activity) => activity.id === event.callId)
          const updated = { id: event.callId, tool: event.tool, status: event.status, summary: event.summary, sources: event.sources, data: event.data }
          if (index >= 0) tools[index] = { ...tools[index], ...updated }
          else tools.push(updated)
        }
        return { ...message, tools }
      }),
    }))
  }

  const generate = async (conversationId: string, context: Message[], displayMessages: Message[]) => {
    const assistantId = crypto.randomUUID()
    const controller = new AbortController()
    abortControllerRef.current = controller
    resetTransient()
    stopSpeaking()
    const attachedDocs = conversations.find((conversation) => conversation.id === conversationId)?.documents ?? []
    if (attachedDocs.length) setDocumentStatus(`Retrieving relevant passages from: ${attachedDocs.map((document) => document.name).join(', ')}`)
    updateConversation(conversationId, (conversation) => ({ ...conversation, messages: [...displayMessages, { id: assistantId, role: 'assistant', content: '', time: timestamp(), source: 'live' }], updatedAt: nowIso() }))
    setTyping(true)
    let completion = ''
    try {
      const styleInstruction: Record<ResponsePreference, string> = {
        concise: 'Keep responses concise and focused. Prefer the shortest complete answer.',
        balanced: 'Give a clear, useful answer with enough detail to act on it.',
        detailed: 'Give a thorough answer with useful context, steps, and examples when appropriate.',
      }
      const recent = context.slice(-(contextTurnLimit * 2))
      const requestMessages: ApiChatMessage[] = [
        { role: 'system', content: styleInstruction[responseStyle] },
        ...recent.map((message) => ({
          role: message.role,
          content: message.content,
          ...(selectedModel?.provider === 'ollama' && selectedModel.capabilities.includes('vision') && message.imageDataUrls?.length ? { images: message.imageDataUrls.map((dataUrl) => dataUrl.slice(dataUrl.indexOf(',') + 1)) } : {}),
        })),
      ]
      await streamChat({
        messages: requestMessages,
        model,
        conversationId: authUser ? conversationId : null,
        contextTurnLimit,
        enabledTools,
        signal: controller.signal,
        onToken: (token) => {
          completion += token
          updateConversation(conversationId, (conversation) => ({ ...conversation, messages: conversation.messages.map((message) => message.id === assistantId ? { ...message, content: message.content + token } : message) }))
        },
        onSources: (sources) => {
          updateConversation(conversationId, (conversation) => ({ ...conversation, messages: conversation.messages.map((message) => message.id === assistantId ? { ...message, sources } : message) }))
          if (attachedDocs.length) setDocumentStatus(sources.length ? `Analyzed relevant excerpts from: ${sources.join(', ')}` : 'No matching document excerpts were retrieved for this question.')
        },
        onToolEvent: (event) => updateToolActivity(conversationId, assistantId, event),
        onConfirmation: (request) => { setConfirmationError(''); setConfirmation(request) },
        onNotice: (message) => setToolNotice(message),
        onDone: (done: StreamDone) => {
          updateConversation(conversationId, (conversation) => ({
            ...conversation,
            messages: conversation.messages.map((message) => message.id === assistantId ? { ...message, sources: done.sources, searchSources: done.searchSources } : message),
          }))
        },
      })
      if (speakReplies && completion.trim()) void speakResponse(assistantId, completion)
    } catch (error) {
      const stopped = (error as DOMException).name === 'AbortError'
      const detail = error instanceof Error ? error.message : 'The selected provider could not be reached.'
      updateConversation(conversationId, (conversation) => ({ ...conversation, messages: conversation.messages.map((message) => message.id === assistantId ? { ...message, content: stopped ? message.content || 'Generation stopped.' : `**Unable to generate a response.**\n\n${detail}` } : message) }))
    } finally {
      if (abortControllerRef.current === controller) abortControllerRef.current = null
      setTyping(false)
      setConfirmation(null)
      setConfirmationPending(false)
    }
  }

  const send = () => {
    const prompt = input.trim() || (pendingImages.length ? 'Describe and analyze the attached image.' : '')
    if (!prompt || typing || uploadStatus.startsWith('Uploading and extracting') || !activeConversation) return
    if (!authUser && (pendingImages.length || enabledTools.length || documents.length)) { setAuthOpen(true); return }
    if (!models.some((candidate) => candidate.id === model && candidate.available)) { setModelStatus('Choose an available model or configure a provider on the backend.'); return }
    if (pendingImages.length && !supportsVision) { setUploadStatus('The selected model does not support image understanding. Choose an Ollama model marked with vision.'); return }
    const attachments = [
      ...documents.map((document) => ({ name: document.name, kind: 'document' as const })),
      ...pendingImages.map((image) => ({ name: image.name, kind: 'image' as const })),
    ]
    const userMessage: Message = { id: crypto.randomUUID(), role: 'user', content: prompt, time: timestamp(), source: 'live', attachments, imageDataUrls: pendingImages.map((image) => image.dataUrl) }
    const nextMessages = [...activeConversation.messages, userMessage]
    const title = activeConversation.messages.some((message) => message.role === 'user') ? activeConversation.title : prompt.slice(0, 42)
    updateConversation(activeId, (conversation) => ({ ...conversation, title, messages: nextMessages, updatedAt: nowIso() }))
    setInput('')
    setPendingImages([])
    setUploadStatus('')
    void generate(activeId, nextMessages, nextMessages)
  }

  const regenerate = (messageId: string) => {
    if (typing || !activeConversation) return
    const index = activeConversation.messages.findIndex((message) => message.id === messageId)
    if (index < 1 || index !== activeConversation.messages.length - 1 || activeConversation.messages[index].source !== 'live') return
    const displayMessages = activeConversation.messages.filter((message) => message.id !== messageId)
    void generate(activeId, activeConversation.messages.slice(0, index), displayMessages)
  }

  const handleConfirmation = async (approved: boolean) => {
    if (!confirmation || confirmationPending) return
    setConfirmationPending(true)
    setConfirmationError('')
    try {
      await resolveToolConfirmation(confirmation.confirmationId, approved)
      setConfirmation(null)
    } catch (error) {
      setConfirmationError(error instanceof Error ? error.message : 'The approval could not be delivered.')
    } finally {
      setConfirmationPending(false)
    }
  }

  const exportConversation = () => {
    if (!activeConversation) return
    if (!authUser) { setAuthOpen(true); return }
    downloadText(`${safeFilename(activeConversation.title)}.md`, conversationToMarkdown({ ...activeConversation, messages }))
  }

  const sortedConversations = useMemo(() => [...conversations].sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt)), [conversations])

  return <div className={`nexa-shell ${resolvedTheme} text-scale-${textSize}`}>
    <Sidebar collapsed={collapsed} model={modelLabel} conversations={sortedConversations} activeId={activeId} onCollapse={() => setCollapsed((value) => !value)} onNewChat={newChat} onSettings={() => { openSettings(); if (window.matchMedia('(max-width: 720px)').matches) setCollapsed(true) }} onSelectConversation={selectConversation} onRenameConversation={renameConversation} onDeleteConversation={deleteConversation} />
    {!collapsed && <button className="mobile-nav-backdrop" aria-label="Close navigation" onClick={() => setCollapsed(true)} />}
    <main className="chat-main">
      <header className="chat-header">
        <button className="mobile-menu icon-button" onClick={() => setCollapsed(false)} aria-label="Open navigation"><Menu size={19} /></button>
        <div><label className="header-model"><select aria-label="Select AI model" value={model} onChange={(event) => setModel(event.target.value)} disabled={typing || !models.length}>
          <option value="" disabled>Select model</option>{models.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.available ? `${item.provider} · ${item.name}` : `${item.provider} · ${item.name} (configure provider)`}</option>)}
        </select><i /></label><span className="header-caption">{modelStatus}{selectedModel?.context_window ? ` · ${selectedModel.context_window.toLocaleString()} token context` : ''}{selectedModel?.capabilities.length ? ` · ${selectedModel.capabilities.join(', ')}` : ''}</span></div>
        <div className="header-actions"><button className="header-export" onClick={exportConversation} disabled={!messages.length} title="Export this conversation as Markdown"><Download size={15} /> Export</button>{authUser ? <button className="header-auth" onClick={() => void signOut().then(() => { setAuthUser(null); setEnabledTools([]); setShowTools(false); setShowSettings(false); setConversations((current) => current.map((item) => ({ ...item, documents: [] }))) })} title={`Sign out ${authUser.email}`}><LogOut size={14} /> Sign out</button> : <button className="header-auth" onClick={() => setAuthOpen(true)}><LogIn size={14} /> Sign in</button>}<button className="header-new" onClick={newChat}><Plus size={16} /> New chat</button></div>
      </header>
      {storageStatus && <div className="storage-warning" role="alert">{storageStatus}<span>Conversation history is temporary browser data, not model long-term memory.</span></div>}
      {documentStatus && <div className="document-status" role="status">{documentStatus}</div>}
      {toolNotice && <div className="tool-notice" role="status"><span>{toolNotice}</span><button onClick={() => setToolNotice('')} aria-label="Dismiss notice">×</button></div>}
      <div className={`conversation-area ${messages.length ? 'has-messages' : ''}`}>
        {messages.length ? <div className="message-list">{messages.map((message, index) => <ChatMessage key={message.id} message={message} onRegenerate={index === messages.length - 1 ? () => regenerate(message.id) : undefined} onSpeak={message.role === 'assistant' ? () => void speakResponse(message.id, message.content) : undefined} onRequireAuth={() => { if (!authUser) { setAuthOpen(true); return false } return true }} speaking={speakingId === message.id} />)}{typing && <div className="typing-indicator"><span /><span /><span /> V-Sutra is thinking</div>}</div> : <WelcomeScreen onSelect={setInput} />}
      </div>
      <ChatComposer value={input} onChange={setInput} onSend={send} isTyping={typing} onStop={() => { abortControllerRef.current?.abort(); resetTransient() }} modelLabel={modelLabel} attachments={composerAttachments} uploadStatus={voiceBusy ? 'Transcribing audio…' : uploadStatus} uploadProgress={uploadProgress} disableSend={typing || uploadStatus.startsWith('Uploading and extracting') || voiceBusy} onFilesSelected={(files) => void handleFiles(files)} onRemoveAttachment={removeAttachment} onOpenTools={openTools} activeToolCount={enabledTools.length} onVoiceInput={() => void startVoiceInput()} isListening={isListening} voiceAvailable={voiceAvailable} voiceHint={voiceHint} />
    </main>
    {showSettings && <SettingsPanel onClose={closeSettings} onClearMessages={clearCurrentConversation} onClearAllHistory={clearAllHistory} theme={theme} onThemeChange={setTheme} textSize={textSize} onTextSizeChange={setTextSize} responseStyle={responseStyle} onResponseStyleChange={setResponseStyle} contextTurnLimit={contextTurnLimit} onContextTurnLimitChange={setContextTurnLimit} models={models} providers={providers} selectedModel={model} onSelectModel={setModel} voiceStatus={voiceStatus} voiceStatusError={voiceStatusError} speakReplies={speakReplies} onSpeakRepliesChange={setSpeakReplies} voiceRate={voiceRate} onVoiceRateChange={setVoiceRate} voiceName={voiceName} onVoiceNameChange={setVoiceName} voiceNames={voiceNames} />}
    {showTools && <ToolsPanel onClose={closeTools} model={model} modelLabel={modelLabel} toolsStatus={toolsStatus} toolsStatusError={toolsStatusError} enabledTools={enabledTools} onToggleTool={(name, enabled) => setEnabledTools((current) => enabled ? Array.from(new Set([...current, name])) : current.filter((item) => item !== name))} documents={documents} onInsertText={setInput} onNotice={setToolNotice} />}
    {confirmation && <ToolConfirmation request={confirmation} pending={confirmationPending} error={confirmationError} onResolve={(approved) => void handleConfirmation(approved)} />}
    <AuthDialog key={resetToken || 'auth'} open={authOpen} onClose={closeAuth} resetToken={resetToken} passwordResetEmailEnabled={passwordResetEmailEnabled} onAuthenticated={(user, resetEmailEnabled) => { setAuthUser(user); setPasswordResetEmailEnabled(resetEmailEnabled); setAuthOpen(false); setResetToken(''); if (queuedFiles.length) { void handleFiles(queuedFiles, true); setQueuedFiles([]) } if (new URLSearchParams(window.location.search).has('reset')) window.history.replaceState({}, '', window.location.pathname) }} />
  </div>
}
