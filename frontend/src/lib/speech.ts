export type BrowserRecognitionResult = {
  results: { length: number; [index: number]: { isFinal: boolean; length: number; [index: number]: { transcript: string } } }
}

export type BrowserRecognition = {
  continuous: boolean
  interimResults: boolean
  lang: string
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: BrowserRecognitionResult) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
}

type RecognitionConstructor = new () => BrowserRecognition

declare global {
  interface Window {
    SpeechRecognition?: RecognitionConstructor
    webkitSpeechRecognition?: RecognitionConstructor
  }
}

export function browserRecognitionSupported(): boolean {
  return typeof window !== 'undefined' && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
}

export function createBrowserRecognition(language = 'en-US'): BrowserRecognition | null {
  if (typeof window === 'undefined') return null
  const Constructor = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!Constructor) return null
  const recognition = new Constructor()
  recognition.continuous = false
  recognition.interimResults = false
  recognition.lang = language
  return recognition
}

export function browserSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export function browserVoices(): SpeechSynthesisVoice[] {
  if (!browserSynthesisSupported()) return []
  return window.speechSynthesis.getVoices().filter((voice) => voice.lang.toLowerCase().startsWith('en'))
}

export function speakWithBrowser(text: string, rate = 1, voiceName = ''): Promise<void> {
  if (!browserSynthesisSupported()) return Promise.reject(new Error('This browser does not provide speech synthesis.'))
  return new Promise((resolve, reject) => {
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = Math.min(2, Math.max(0.5, rate))
    const voice = voiceName ? window.speechSynthesis.getVoices().find((item) => item.name === voiceName) : undefined
    if (voice) utterance.voice = voice
    utterance.onend = () => resolve()
    utterance.onerror = (event) => reject(new Error(event.error === 'canceled' ? 'Speech was stopped.' : 'The browser could not speak this response.'))
    window.speechSynthesis.speak(utterance)
  })
}

export function stopBrowserSpeech(): void {
  if (browserSynthesisSupported()) window.speechSynthesis.cancel()
}

export function canRecordAudio(): boolean {
  return typeof window !== 'undefined'
    && typeof navigator !== 'undefined'
    && Boolean(navigator.mediaDevices?.getUserMedia)
    && typeof MediaRecorder !== 'undefined'
}
