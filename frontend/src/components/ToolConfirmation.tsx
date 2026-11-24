'use client'

import { AlertTriangle, Check, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useDialogFocus } from '@/lib/use-dialog-focus'
import type { ConfirmationRequest } from '@/lib/types'

type Props = {
  request: ConfirmationRequest
  pending: boolean
  error: string
  onResolve: (approved: boolean) => void
}

export function ToolConfirmation({ request, pending, error, onResolve }: Props) {
  const [remaining, setRemaining] = useState(Math.max(0, Math.round(request.expiresIn)))
  const expired = remaining <= 0
  const dialogRef = useRef<HTMLElement>(null)
  useDialogFocus(dialogRef, () => { if (!pending && !expired) onResolve(false) })
  useEffect(() => {
    const timer = window.setInterval(() => setRemaining((value) => Math.max(0, value - 1)), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const code = typeof request.arguments.code === 'string' ? request.arguments.code : ''
  const language = typeof request.arguments.language === 'string' ? request.arguments.language : 'code'
  return <div className="settings-backdrop confirm-backdrop" role="presentation">
    <section ref={dialogRef} tabIndex={-1} className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-detail">
      <div className="confirm-heading">
        <span className="confirm-icon"><ShieldCheck size={20} /></span>
        <div><p className="eyebrow">Approval required</p><h2 id="confirm-title">Run code in the sandbox?</h2></div>
      </div>
      <p id="confirm-detail" className="confirm-detail">The model wants to run this {language} program. It executes in an isolated container with no network access, a read-only filesystem, a non-root user, and strict CPU, memory, process, output, and time limits. It never runs directly on this host.</p>
      {code ? <pre className="confirm-code"><code>{code}</code></pre> : <div className="confirm-empty"><AlertTriangle size={14} /> No code was included in this request.</div>}
      <div className="confirm-meta"><span>Tool: {request.tool}</span><span>{expired ? 'Request expired' : `Expires in ${remaining}s`}</span></div>
      {error ? <div className="message-action-error" role="alert">{error}</div> : null}
      <div className="confirm-actions">
        <button className="confirm-deny" onClick={() => onResolve(false)} disabled={pending || expired}><X size={15} />Deny</button>
        <button className="confirm-approve" onClick={() => onResolve(true)} disabled={pending || expired || !code}>{pending ? 'Waiting…' : <><Check size={15} />Approve and run</>}</button>
      </div>
    </section>
  </div>
}
