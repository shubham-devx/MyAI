'use client'

import {
  AlertTriangle, Braces, Calculator, Check, ChevronDown, Code2, Copy, ExternalLink,
  FileSpreadsheet, Globe2, Loader2, Play, Search, ShieldCheck, Sparkles, Terminal,
  Wrench, X,
} from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { analyzeCsvFile, calculateExpression, generateCode, generateStructured, runSandbox, searchWeb } from '@/lib/chat-stream'
import { copyText } from '@/lib/export'
import { useDialogFocus } from '@/lib/use-dialog-focus'
import type { CsvAnalysis, SandboxResult, SearchSource, ToolsStatus, UploadedDocument } from '@/lib/types'

type Tab = 'chat' | 'search' | 'calculator' | 'code' | 'csv' | 'sandbox' | 'structured'

type Props = {
  onClose: () => void
  model: string
  modelLabel: string
  toolsStatus: ToolsStatus | null
  toolsStatusError: string
  enabledTools: string[]
  onToggleTool: (name: string, enabled: boolean) => void
  documents: UploadedDocument[]
  onInsertText: (text: string) => void
  onNotice: (message: string) => void
}

const toolIcons: Record<string, React.ReactNode> = {
  web_search: <Globe2 size={16} />,
  calculator: <Calculator size={16} />,
  analyze_csv: <FileSpreadsheet size={16} />,
  run_code: <Terminal size={16} />,
}

const schemaPresets: Record<string, { label: string; schema: Record<string, unknown> }> = {
  summary: {
    label: 'Summary',
    schema: {
      type: 'object',
      properties: {
        summary: { type: 'string', maxLength: 1200 },
        key_points: { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 8 },
        confidence: { type: 'number', minimum: 0, maximum: 1 },
      },
      required: ['summary', 'key_points', 'confidence'],
      additionalProperties: false,
    },
  },
  tasks: {
    label: 'Task list',
    schema: {
      type: 'object',
      properties: {
        tasks: {
          type: 'array',
          minItems: 1,
          items: {
            type: 'object',
            properties: {
              title: { type: 'string' },
              owner: { type: 'string' },
              priority: { type: 'string', enum: ['low', 'medium', 'high'] },
              due_date: { type: 'string' },
            },
            required: ['title', 'priority'],
          },
        },
      },
      required: ['tasks'],
      additionalProperties: false,
    },
  },
  entities: {
    label: 'Entities',
    schema: {
      type: 'object',
      properties: {
        people: { type: 'array', items: { type: 'string' } },
        organizations: { type: 'array', items: { type: 'string' } },
        locations: { type: 'array', items: { type: 'string' } },
        dates: { type: 'array', items: { type: 'string' } },
      },
      required: ['people', 'organizations', 'locations', 'dates'],
    },
  },
  custom: { label: 'Custom schema', schema: { type: 'object' } },
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : 'The action could not be completed.'
}

function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

function ResultError({ message }: { message: string }) {
  if (!message) return null
  return <div className="tool-error" role="alert"><AlertTriangle size={14} />{message}</div>
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  return <button className="tool-mini-button" onClick={async () => {
    try {
      await copyText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch { /* The parent surfaces clipboard errors through its own actions. */ }
  }}>{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? 'Copied' : label}</button>
}

export function ToolsPanel({ onClose, model, modelLabel, toolsStatus, toolsStatusError, enabledTools, onToggleTool, documents, onInsertText, onNotice }: Props) {
  const [tab, setTab] = useState<Tab>('chat')
  const dialogRef = useRef<HTMLElement>(null)
  const csvFileRef = useRef<HTMLInputElement>(null)

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchSource[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')

  const [expression, setExpression] = useState('')
  const [calcResult, setCalcResult] = useState('')
  const [calcError, setCalcError] = useState('')
  const [calculating, setCalculating] = useState(false)

  const [codePrompt, setCodePrompt] = useState('')
  const [codeLanguage, setCodeLanguage] = useState('python')
  const [codeRequirements, setCodeRequirements] = useState('')
  const [generatedCode, setGeneratedCode] = useState('')
  const [codeExplanation, setCodeExplanation] = useState('')
  const [generatingCode, setGeneratingCode] = useState(false)
  const [codeError, setCodeError] = useState('')

  const [csvAnalysis, setCsvAnalysis] = useState<CsvAnalysis | null>(null)
  const [csvSummary, setCsvSummary] = useState('')
  const [csvBusy, setCsvBusy] = useState(false)
  const [csvError, setCsvError] = useState('')

  const [sandboxLanguage, setSandboxLanguage] = useState<'python' | 'javascript'>('python')
  const [sandboxCode, setSandboxCode] = useState('')
  const [sandboxStdin, setSandboxStdin] = useState('')
  const [sandboxConfirmed, setSandboxConfirmed] = useState(false)
  const [sandboxResult, setSandboxResult] = useState<SandboxResult | null>(null)
  const [sandboxBusy, setSandboxBusy] = useState(false)
  const [sandboxError, setSandboxError] = useState('')

  const [structuredPrompt, setStructuredPrompt] = useState('')
  const [structuredPreset, setStructuredPreset] = useState('summary')
  const [structuredSchema, setStructuredSchema] = useState(JSON.stringify(schemaPresets.summary.schema, null, 2))
  const [structuredResult, setStructuredResult] = useState('')
  const [structuredBusy, setStructuredBusy] = useState(false)
  const [structuredError, setStructuredError] = useState('')

  const csvDocuments = useMemo(() => documents.filter((document) => document.type === 'csv' || document.name.toLowerCase().endsWith('.csv')), [documents])
  const sandboxStatus = toolsStatus?.sandbox
  const searchStatus = toolsStatus?.search

  useDialogFocus(dialogRef, onClose)

  const doSearch = async () => {
    if (!searchQuery.trim() || searching) return
    setSearching(true); setSearchError(''); setSearchResults([])
    try {
      const result = await searchWeb(searchQuery.trim())
      setSearchResults(result.results)
      if (!result.results.length) setSearchError('The search provider returned no results.')
    } catch (error) { setSearchError(errorText(error)) } finally { setSearching(false) }
  }

  const doCalculate = async () => {
    if (!expression.trim() || calculating) return
    setCalculating(true); setCalcError(''); setCalcResult('')
    try {
      const result = await calculateExpression(expression.trim())
      setCalcResult(`${result.expression} = ${result.result}`)
    } catch (error) { setCalcError(errorText(error)) } finally { setCalculating(false) }
  }

  const doGenerateCode = async () => {
    if (!codePrompt.trim() || generatingCode) return
    setGeneratingCode(true); setCodeError(''); setGeneratedCode(''); setCodeExplanation('')
    try {
      if (!model) throw new Error('Select an available model first.')
      const result = await generateCode({ prompt: codePrompt.trim(), language: codeLanguage, model, requirements: codeRequirements.trim() })
      setGeneratedCode(result.code); setCodeExplanation(result.explanation)
    } catch (error) { setCodeError(errorText(error)) } finally { setGeneratingCode(false) }
  }

  const doAnalyzeCsv = async (file: File | undefined) => {
    if (!file || csvBusy) return
    setCsvBusy(true); setCsvError(''); setCsvAnalysis(null); setCsvSummary('')
    try {
      const result = await analyzeCsvFile(file)
      setCsvAnalysis(result.analysis); setCsvSummary(result.summary)
    } catch (error) { setCsvError(errorText(error)) } finally { setCsvBusy(false) }
  }

  const doRunSandbox = async () => {
    if (!sandboxCode.trim() || sandboxBusy) return
    if (!sandboxConfirmed) { setSandboxError('Confirm that you want to run this isolated code.'); return }
    setSandboxBusy(true); setSandboxError(''); setSandboxResult(null)
    try {
      const result = await runSandbox({ language: sandboxLanguage, code: sandboxCode, stdin: sandboxStdin, confirmed: true })
      setSandboxResult(result)
    } catch (error) { setSandboxError(errorText(error)) } finally { setSandboxBusy(false) }
  }

  const doStructured = async () => {
    if (!structuredPrompt.trim() || structuredBusy) return
    setStructuredBusy(true); setStructuredError(''); setStructuredResult('')
    try {
      if (!model) throw new Error('Select an available model first.')
      const schema = JSON.parse(structuredSchema) as Record<string, unknown>
      if (!schema || typeof schema !== 'object' || Array.isArray(schema)) throw new Error('The schema must be a JSON object.')
      const result = await generateStructured({ prompt: structuredPrompt.trim(), schema, model })
      setStructuredResult(JSON.stringify(result.json, null, 2))
    } catch (error) { setStructuredError(errorText(error)) } finally { setStructuredBusy(false) }
  }

  const insert = (text: string) => { onInsertText(text); onNotice('Inserted into the composer.') }

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: 'chat', label: 'Chat tools', icon: <Wrench size={14} /> },
    { id: 'search', label: 'Search', icon: <Globe2 size={14} /> },
    { id: 'calculator', label: 'Calculate', icon: <Calculator size={14} /> },
    { id: 'code', label: 'Code', icon: <Code2 size={14} /> },
    { id: 'csv', label: 'CSV', icon: <FileSpreadsheet size={14} /> },
    { id: 'sandbox', label: 'Sandbox', icon: <Terminal size={14} /> },
    { id: 'structured', label: 'JSON', icon: <Braces size={14} /> },
  ]

  return <div className="settings-backdrop tools-backdrop" role="presentation" onMouseDown={onClose}>
    <section ref={dialogRef} tabIndex={-1} className="tools-panel" role="dialog" aria-modal="true" aria-labelledby="tools-title" onMouseDown={(event) => event.stopPropagation()}>
      <div className="settings-header tools-header">
        <div><p className="eyebrow">Advanced AI capabilities</p><h2 id="tools-title">Toolkit</h2><small>{modelLabel}</small></div>
        <button className="icon-button" onClick={onClose} aria-label="Close tools"><X size={18} /></button>
      </div>

      <div className="tool-tabs" role="tablist" aria-label="Tool categories">
        {tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)}>{item.icon}<span>{item.label}</span></button>)}
      </div>

      <div className="tools-content">
        {tab === 'chat' ? <section className="tool-section" aria-labelledby="chat-tools-title">
          <h3 id="chat-tools-title">Tools the model may use</h3>
          <p className="tool-copy">Enable tools for this chat. Web search is an external network request; code execution always asks for your approval first. Tool calls and failures are shown in the response.</p>
          {toolsStatus?.tools.map((tool) => <label className={`tool-toggle-row ${tool.available ? '' : 'is-unavailable'}`} key={tool.name}>
            <span className="tool-icon-box">{toolIcons[tool.name] ?? <Wrench size={16} />}</span>
            <span className="tool-toggle-copy">
              <strong>{tool.label}{tool.external ? <em className="tool-badge external">External</em> : null}{tool.requires_confirmation ? <em className="tool-badge confirm">Confirm</em> : null}</strong>
              <small>{tool.available ? tool.description : tool.reason || 'Not available'}</small>
            </span>
            <input type="checkbox" checked={enabledTools.includes(tool.name)} disabled={!tool.available} onChange={(event) => onToggleTool(tool.name, event.target.checked)} aria-label={`Enable ${tool.label}`} />
          </label>)}
          {!toolsStatus && toolsStatusError ? <div className="tool-error" role="alert">Could not load tool status: {toolsStatusError}</div> : null}
          {!toolsStatus && !toolsStatusError ? <div className="tool-loading"><Loader2 size={14} className="spin" /> Loading tool status…</div> : null}
          <div className="tool-status-grid">
            <div className="tool-status-card"><span className="tool-status-label"><Globe2 size={13} /> Web search</span><strong>{searchStatus?.label ?? (toolsStatusError ? 'Unavailable' : 'Loading')}</strong><small>{searchStatus?.configured ? 'Ready for external searches.' : searchStatus?.hint ?? (toolsStatusError || 'Not configured.')}</small></div>
            <div className="tool-status-card"><span className="tool-status-label"><ShieldCheck size={13} /> Sandbox</span><strong>{sandboxStatus?.available ? `${sandboxStatus.engine} ready` : sandboxStatus?.enabled ? 'Unavailable' : 'Disabled'}</strong><small>{sandboxStatus?.hint ?? `Network: ${sandboxStatus?.limits.network ?? 'disabled'} · ${sandboxStatus?.limits.memory_mb ?? 256} MB max`}</small></div>
          </div>
          {csvDocuments.length ? <div className="tool-hint"><FileSpreadsheet size={13} /> CSV analysis can use: {csvDocuments.map((document) => document.name).join(', ')}</div> : <div className="tool-hint"><FileSpreadsheet size={13} /> Attach a CSV in the chat composer to let the model analyze it in place.</div>}
        </section> : null}

        {tab === 'search' ? <section className="tool-section" aria-labelledby="search-title">
          <h3 id="search-title">Web search <span className="tool-badge external">External</span></h3>
          <p className="tool-copy">Uses the backend-configured provider: {searchStatus?.label ?? 'loading'}. Results are real network responses and are never generated by the model.</p>
          <div className="tool-form-row"><input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void doSearch() }} placeholder="Search the web…" aria-label="Web search query" /><button className="tool-primary" onClick={() => void doSearch()} disabled={searching || !searchStatus?.configured}>{searching ? <Loader2 size={14} className="spin" /> : <Search size={14} />}Search</button></div>
          <ResultError message={searchError} />
          {searchResults.length ? <div className="search-result-list">{searchResults.map((result, index) => {
            const url = safeHttpUrl(result.url)
            return <article className="search-result" key={`${result.url}-${index}`}>
              <strong>{result.title || result.url}</strong>
              {url ? <a href={url} target="_blank" rel="noreferrer noopener">{url}<ExternalLink size={11} /></a> : <span>{result.url}</span>}
              {result.snippet ? <p>{result.snippet}</p> : null}
              <div className="tool-result-actions"><CopyButton text={`${result.title}\n${result.url}\n${result.snippet ?? ''}`} label="Copy" /><button className="tool-mini-button" onClick={() => insert(`Web search results for "${searchQuery}":\n\n${searchResults.map((item, position) => `${position + 1}. ${item.title}\n${item.url}\n${item.snippet ?? ''}`).join('\n\n')}`)}><Sparkles size={13} />Use in chat</button></div>
            </article>
          })}</div> : null}
          {!searchStatus?.configured ? <div className="tool-hint"><AlertTriangle size={13} /> {searchStatus?.hint ?? 'Configure a search provider on the backend.'}</div> : null}
        </section> : null}

        {tab === 'calculator' ? <section className="tool-section" aria-labelledby="calc-title">
          <h3 id="calc-title">Calculator</h3>
          <p className="tool-copy">Exact arithmetic evaluated on the backend with a restricted expression parser. No host code execution.</p>
          <div className="tool-form-row"><input value={expression} onChange={(event) => setExpression(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void doCalculate() }} placeholder="e.g. (1250 * 1.18) / 3 + sqrt(2)" aria-label="Calculator expression" /><button className="tool-primary" onClick={() => void doCalculate()} disabled={calculating}>{calculating ? <Loader2 size={14} className="spin" /> : <Calculator size={14} />}Calculate</button></div>
          <ResultError message={calcError} />
          {calcResult ? <div className="tool-result-box"><strong>{calcResult}</strong><div className="tool-result-actions"><CopyButton text={calcResult} /><button className="tool-mini-button" onClick={() => insert(calcResult)}><Sparkles size={13} />Use in chat</button></div></div> : null}
        </section> : null}

        {tab === 'code' ? <section className="tool-section" aria-labelledby="code-title">
          <h3 id="code-title">Code generation</h3>
          <p className="tool-copy">Generated by your selected model ({modelLabel}). Review code before running it; use the sandbox tab to execute it safely.</p>
          <label className="tool-field"><span>Task</span><textarea value={codePrompt} onChange={(event) => setCodePrompt(event.target.value)} rows={4} placeholder="Describe the code you need…" /></label>
          <div className="tool-form-row"><label className="tool-field compact"><span>Language</span><select value={codeLanguage} onChange={(event) => setCodeLanguage(event.target.value)}>{['python', 'javascript', 'typescript', 'java', 'go', 'bash', 'sql', 'other'].map((language) => <option key={language}>{language}</option>)}</select></label><label className="tool-field compact grow"><span>Extra requirements (optional)</span><input value={codeRequirements} onChange={(event) => setCodeRequirements(event.target.value)} placeholder="Dependencies, style, constraints…" /></label></div>
          <button className="tool-primary" onClick={() => void doGenerateCode()} disabled={generatingCode || !codePrompt.trim()}>{generatingCode ? <Loader2 size={14} className="spin" /> : <Code2 size={14} />}Generate code</button>
          <ResultError message={codeError} />
          {generatedCode ? <div className="tool-code-result"><div className="tool-result-heading"><span>{codeLanguage} output</span><div className="tool-result-actions"><CopyButton text={generatedCode} /><button className="tool-mini-button" onClick={() => insert('```' + codeLanguage + '\n' + generatedCode + '\n```')}><Sparkles size={13} />Use in chat</button>{codeLanguage === 'python' || codeLanguage === 'javascript' ? <button className="tool-mini-button" onClick={() => { setSandboxLanguage(codeLanguage as 'python' | 'javascript'); setSandboxCode(generatedCode); setSandboxResult(null); setTab('sandbox') }}><ShieldCheck size={13} />Open sandbox</button> : null}</div></div><pre><code>{generatedCode}</code></pre>{codeExplanation && codeExplanation.trim() !== generatedCode.trim() ? <details><summary>Model explanation</summary><p>{codeExplanation}</p></details> : null}</div> : null}
        </section> : null}

        {tab === 'csv' ? <section className="tool-section" aria-labelledby="csv-title">
          <h3 id="csv-title">CSV data analysis</h3>
          <p className="tool-copy">Parses a UTF-8 CSV and computes real statistics locally on the backend: types, missing values, distributions, correlations, and a preview. Formulas and cell text are never executed.</p>
          <input ref={csvFileRef} type="file" accept=".csv,text/csv" className="visually-hidden-file" onChange={(event) => { void doAnalyzeCsv(event.currentTarget.files?.[0]); event.currentTarget.value = '' }} />
          <div className="tool-form-row"><button className="tool-secondary" onClick={() => csvFileRef.current?.click()} disabled={csvBusy}>{csvBusy ? <Loader2 size={14} className="spin" /> : <FileSpreadsheet size={14} />}Choose CSV</button><span className="tool-inline-note">Up to 15 MB · UTF-8</span></div>
          <ResultError message={csvError} />
          {csvAnalysis ? <div className="tool-csv-result">
            <div className="csv-stats"><span><strong>{csvAnalysis.row_count.toLocaleString()}</strong> rows</span><span><strong>{csvAnalysis.column_count}</strong> columns</span><span><strong>{csvAnalysis.columns.reduce((sum, column) => sum + column.missing, 0).toLocaleString()}</strong> missing</span></div>
            <ul className="csv-insights">{csvAnalysis.insights.map((insight) => <li key={insight}>{insight}</li>)}</ul>
            <div className="csv-table-wrap"><table><thead><tr>{csvAnalysis.columns.map((column) => <th key={column.name}>{column.name}<small>{column.type}</small></th>)}</tr></thead><tbody>{csvAnalysis.preview.map((row, index) => <tr key={index}>{csvAnalysis.columns.map((column) => <td key={column.name}>{row[column.name] || '—'}</td>)}</tr>)}</tbody></table></div>
            {csvAnalysis.correlations.length ? <div className="csv-correlations">{csvAnalysis.correlations.slice(0, 8).map((item) => <span key={`${item.left}-${item.right}`}>{item.left} ↔ {item.right}: <strong>{item.r}</strong> ({item.strength})</span>)}</div> : null}
            <div className="tool-result-actions"><CopyButton text={csvSummary} label="Copy summary" /><button className="tool-mini-button" onClick={() => insert(csvSummary)}><Sparkles size={13} />Use summary in chat</button></div>
          </div> : null}
          {csvDocuments.length ? <div className="tool-hint"><FileSpreadsheet size={13} /> Attached in this chat: {csvDocuments.map((document) => document.name).join(', ')}. The model can call CSV analysis automatically when that tool is enabled.</div> : null}
        </section> : null}

        {tab === 'sandbox' ? <section className="tool-section" aria-labelledby="sandbox-title">
          <h3 id="sandbox-title">Sandboxed execution <span className="tool-badge confirm">Confirm</span></h3>
          <p className="tool-copy">Runs only inside {sandboxStatus?.engine ? `${sandboxStatus.engine}` : 'Docker or Podman'} with no network, a read-only root filesystem, non-root user, {sandboxStatus?.limits.memory_mb ?? 256} MB memory, {sandboxStatus?.limits.cpus ?? 0.5} CPU, {sandboxStatus?.limits.timeout_seconds ?? 10}s timeout. There is no host fallback.</p>
          {!sandboxStatus?.available ? <div className="tool-hint"><AlertTriangle size={13} /> {sandboxStatus?.hint ?? 'The sandbox is unavailable. Install Docker or Podman and pull an image.'}</div> : null}
          <div className="tool-form-row"><label className="tool-field compact"><span>Language</span><select value={sandboxLanguage} onChange={(event) => setSandboxLanguage(event.target.value as 'python' | 'javascript')}><option value="python">Python</option><option value="javascript">JavaScript</option></select></label><span className="tool-inline-note">{sandboxStatus?.languages.find((item) => item.name === sandboxLanguage)?.image ?? ''}</span></div>
          <label className="tool-field"><span>Code</span><textarea className="code-textarea" value={sandboxCode} onChange={(event) => { setSandboxCode(event.target.value); setSandboxConfirmed(false) }} rows={8} placeholder={sandboxLanguage === 'python' ? 'print("hello from the sandbox")' : 'console.log("hello from the sandbox")'} spellCheck={false} /></label>
          <label className="tool-field"><span>Standard input (optional)</span><textarea value={sandboxStdin} onChange={(event) => setSandboxStdin(event.target.value)} rows={2} placeholder="Input available on stdin" /></label>
          <label className="tool-confirm-check"><input type="checkbox" checked={sandboxConfirmed} onChange={(event) => setSandboxConfirmed(event.target.checked)} /><span>I understand this code will be executed in an isolated container.</span></label>
          <button className="tool-primary" onClick={() => void doRunSandbox()} disabled={sandboxBusy || !sandboxCode.trim() || !sandboxStatus?.available}>{sandboxBusy ? <Loader2 size={14} className="spin" /> : <Play size={14} />}Run in sandbox</button>
          <ResultError message={sandboxError} />
          {sandboxResult ? <div className="tool-sandbox-result">
            <div className="tool-result-heading"><span>{sandboxResult.ok ? 'Completed' : sandboxResult.timed_out ? 'Timed out' : 'Exited with errors'} · {sandboxResult.duration_ms} ms</span><span>exit {sandboxResult.exit_code ?? '—'}</span></div>
            <div className="sandbox-stream"><span>stdout</span><pre><code>{sandboxResult.stdout || '(empty)'}</code></pre></div>
            {sandboxResult.stderr ? <div className="sandbox-stream stderr"><span>stderr</span><pre><code>{sandboxResult.stderr}</code></pre></div> : null}
            {sandboxResult.truncated ? <div className="tool-hint"><AlertTriangle size={13} /> Output was truncated at the configured limit.</div> : null}
          </div> : null}
        </section> : null}

        {tab === 'structured' ? <section className="tool-section" aria-labelledby="structured-title">
          <h3 id="structured-title">Structured JSON output</h3>
          <p className="tool-copy">The selected model returns JSON, then the backend validates it against your schema. Invalid output is repaired once or rejected; it is never presented as valid silently.</p>
          <label className="tool-field"><span>Prompt</span><textarea value={structuredPrompt} onChange={(event) => setStructuredPrompt(event.target.value)} rows={4} placeholder="Describe what to extract or generate…" /></label>
          <label className="tool-field compact"><span>Schema preset</span><span className="select-wrap"><select value={structuredPreset} onChange={(event) => { const key = event.target.value; setStructuredPreset(key); if (key !== 'custom') setStructuredSchema(JSON.stringify(schemaPresets[key].schema, null, 2)) }}>{Object.entries(schemaPresets).map(([key, preset]) => <option value={key} key={key}>{preset.label}</option>)}</select><ChevronDown size={14} /></span></label>
          <label className="tool-field"><span>JSON Schema</span><textarea className="code-textarea" value={structuredSchema} onChange={(event) => { setStructuredSchema(event.target.value); setStructuredPreset('custom') }} rows={8} spellCheck={false} /></label>
          <button className="tool-primary" onClick={() => void doStructured()} disabled={structuredBusy || !structuredPrompt.trim()}>{structuredBusy ? <Loader2 size={14} className="spin" /> : <Braces size={14} />}Generate JSON</button>
          <ResultError message={structuredError} />
          {structuredResult ? <div className="tool-code-result"><div className="tool-result-heading"><span>Validated JSON</span><div className="tool-result-actions"><CopyButton text={structuredResult} /><button className="tool-mini-button" onClick={() => insert('```json\n' + structuredResult + '\n```')}><Sparkles size={13} />Use in chat</button></div></div><pre><code>{structuredResult}</code></pre></div> : null}
        </section> : null}
      </div>
    </section>
  </div>
}
