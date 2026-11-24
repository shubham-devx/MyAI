'use client'

import { ChevronDown, Monitor, Moon, Mic, Sun, Trash2, Volume2, X } from 'lucide-react'
import { useRef } from 'react'
import { useDialogFocus } from '@/lib/use-dialog-focus'
import type { ModelOption, ProviderStatus, VoiceStatus } from '@/lib/types'

export type ThemePreference = 'dark' | 'light' | 'system'
export type TextSizePreference = 'small' | 'default' | 'large'
export type ResponsePreference = 'concise' | 'balanced' | 'detailed'
export type ContextTurnLimit = 2 | 4 | 8 | 12 | 20

type Props = {
  onClose: () => void
  onClearMessages: () => void
  onClearAllHistory: () => void
  theme: ThemePreference
  onThemeChange: (value: ThemePreference) => void
  textSize: TextSizePreference
  onTextSizeChange: (value: TextSizePreference) => void
  responseStyle: ResponsePreference
  onResponseStyleChange: (value: ResponsePreference) => void
  contextTurnLimit: ContextTurnLimit
  onContextTurnLimitChange: (value: ContextTurnLimit) => void
  models: ModelOption[]
  providers: ProviderStatus[]
  selectedModel: string
  onSelectModel: (model: string) => void
  voiceStatus: VoiceStatus | null
  voiceStatusError: string
  speakReplies: boolean
  onSpeakRepliesChange: (value: boolean) => void
  voiceRate: number
  onVoiceRateChange: (value: number) => void
  voiceName: string
  onVoiceNameChange: (value: string) => void
  voiceNames: { name: string; lang: string }[]
}

export function SettingsPanel({
  onClose, onClearMessages, onClearAllHistory, theme, onThemeChange, textSize, onTextSizeChange,
  responseStyle, onResponseStyleChange, contextTurnLimit, onContextTurnLimitChange,
  models, providers, selectedModel, onSelectModel,
  voiceStatus, voiceStatusError, speakReplies, onSpeakRepliesChange, voiceRate, onVoiceRateChange, voiceName, onVoiceNameChange, voiceNames,
}: Props) {
  const dialogRef = useRef<HTMLElement>(null)
  useDialogFocus(dialogRef, onClose)

  return <div className="settings-backdrop" role="presentation" onMouseDown={onClose}>
    <section ref={dialogRef} tabIndex={-1} className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title" onMouseDown={(event) => event.stopPropagation()}>
      <div className="settings-header">
        <div><p className="eyebrow">Personalize your workspace</p><h2 id="settings-title">Settings</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close settings"><X size={18} /></button>
      </div>

      <div className="settings-content">
        <section className="settings-group" aria-labelledby="appearance-title">
          <h3 id="appearance-title">Appearance</h3>
          <SettingSelect label="Theme" detail="Choose how V-Sutra looks" icon={<Moon size={17} />} value={theme} onChange={(value) => onThemeChange(value as ThemePreference)}>
            <option value="system">Use device setting</option><option value="dark">Dark</option><option value="light">Light</option>
          </SettingSelect>
          <SettingSelect label="Text size" detail="Adjust chat readability" icon={<Sun size={17} />} value={textSize} onChange={(value) => onTextSizeChange(value as TextSizePreference)}>
            <option value="small">Small</option><option value="default">Default</option><option value="large">Large</option>
          </SettingSelect>
        </section>

        <section className="settings-group" aria-labelledby="response-title">
          <h3 id="response-title">Responses</h3>
          <SettingSelect label="Response style" detail="Applied as an instruction to your selected model" icon={<Monitor size={17} />} value={responseStyle} onChange={(value) => onResponseStyleChange(value as ResponsePreference)}>
            <option value="concise">Concise</option><option value="balanced">Balanced</option><option value="detailed">Detailed</option>
          </SettingSelect>
          <SettingSelect label="Context limit" detail={`Send the most recent ${contextTurnLimit} turns; older turns stay saved here`} icon={<Monitor size={17} />} value={String(contextTurnLimit)} onChange={(value) => onContextTurnLimitChange(Number(value) as ContextTurnLimit)}>
            <option value="2">2 turns</option><option value="4">4 turns</option><option value="8">8 turns</option><option value="12">12 turns</option><option value="20">20 turns</option>
          </SettingSelect>
        </section>

        <section className="settings-group" aria-labelledby="voice-title">
          <h3 id="voice-title">Voice</h3>
          <label className="setting-row voice-toggle-row">
            <span className="setting-icon"><Volume2 size={17} /></span>
            <span className="setting-copy"><strong>Read responses aloud</strong><small>Speak each completed answer with the selected engine</small></span>
            <input type="checkbox" checked={speakReplies} onChange={(event) => onSpeakRepliesChange(event.target.checked)} aria-label="Read responses aloud" />
          </label>
          <SettingSelect label="Speaking rate" detail="Browser engine speed" icon={<Volume2 size={17} />} value={String(voiceRate)} onChange={(value) => onVoiceRateChange(Number(value))}>
            <option value="0.85">Slow</option><option value="1">Normal</option><option value="1.15">Fast</option><option value="1.3">Very fast</option>
          </SettingSelect>
          {voiceStatus?.tts.provider === 'browser' && voiceNames.length ? <SettingSelect label="Browser voice" detail="Choose a locally installed system voice" icon={<Mic size={17} />} value={voiceName} onChange={onVoiceNameChange}>
            <option value="">System default</option>{voiceNames.map((voice) => <option value={voice.name} key={voice.name}>{voice.name} · {voice.lang}</option>)}
          </SettingSelect> : null}
          <div className="voice-engine-status">
            <div><strong>Input</strong><small>{voiceStatus ? `${voiceStatus.stt.label}${voiceStatus.stt.available ? '' : ' · browser fallback'}` : voiceStatusError || 'Checking…'}</small></div>
            <div><strong>Output</strong><small>{voiceStatus?.tts.label ?? (voiceStatusError || 'Checking…')}</small></div>
          </div>
          <p className="settings-footnote">Local and external speech engines are configured on the backend with VOICE_* settings. The browser engines require no credentials.</p>
        </section>

        <section className="settings-group" aria-labelledby="model-title">
          <h3 id="model-title">Models and providers</h3>
          <label className="model-setting"><span><strong>Active model</strong><small>Choose an available local or configured model</small></span><span className="select-wrap"><select value={selectedModel} onChange={(event) => onSelectModel(event.target.value)} aria-label="Active model in settings"><option value="" disabled>Select model</option>{models.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.provider} · {item.name}{item.available ? '' : ' (setup required)'}</option>)}</select><ChevronDown size={15} /></span></label>
          <div className="provider-list" aria-label="Provider status">
            {providers.map((provider) => <div className="provider-status" key={provider.id}><span className={`provider-indicator ${provider.available ? 'is-ready' : ''}`} /><span className="provider-copy"><strong>{provider.name}</strong><small>{provider.available ? 'Ready' : provider.configuration_hint || 'Not configured'}</small></span><span className={`provider-badge ${provider.available ? 'is-ready' : ''}`}>{provider.available ? 'Ready' : 'Setup'}</span></div>)}
          </div>
          <p className="settings-footnote">Provider credentials are configured on the backend and never entered in this browser.</p>
        </section>

        <section className="settings-group settings-data" aria-labelledby="data-title">
          <h3 id="data-title">This session</h3>
          <button className="clear-conversations" onClick={onClearMessages}><Trash2 size={15} /> Clear current conversation</button>
          <button className="clear-conversations clear-all-history" onClick={onClearAllHistory}><Trash2 size={15} /> Clear all temporary history</button>
          <p className="settings-footnote">Temporary chat history is stored in this browser only. It is not long-term model memory and is not available to the model unless included in the context window.</p>
        </section>
      </div>
    </section>
  </div>
}

function SettingSelect({ label, detail, icon, value, onChange, children }: {
  label: string
  detail: string
  icon: React.ReactNode
  value: string
  onChange: (value: string) => void
  children: React.ReactNode
}) {
  return <label className="setting-row">
    <span className="setting-icon">{icon}</span>
    <span className="setting-copy"><strong>{label}</strong><small>{detail}</small></span>
    <span className="select-wrap"><select value={value} onChange={(event) => onChange(event.target.value)} aria-label={label}>{children}</select><ChevronDown size={15} /></span>
  </label>
}
