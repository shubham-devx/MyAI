'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { resetPassword, requestPasswordReset, signIn, signUp, type AuthUser } from '@/lib/auth-client'

type Mode = 'login' | 'signup' | 'forgot' | 'reset'
type Props = {
  open: boolean
  onClose: () => void
  onAuthenticated: (user: AuthUser, resetEmailEnabled: boolean) => void
  resetToken?: string
  passwordResetEmailEnabled: boolean
}

export function AuthDialog({ open, onClose, onAuthenticated, resetToken, passwordResetEmailEnabled }: Props) {
  const [mode, setMode] = useState<Mode>(resetToken ? 'reset' : 'login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const first = document.querySelector<HTMLElement>('.auth-dialog input')
    first?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => { document.removeEventListener('keydown', onKeyDown); previous?.focus() }
  }, [open, mode, onClose])

  if (!open) return null

  const title = mode === 'signup' ? 'Create your account' : mode === 'forgot' ? 'Reset your password' : mode === 'reset' ? 'Choose a new password' : 'Welcome back'
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setBusy(true); setError(''); setNotice('')
    try {
      if (mode === 'signup') {
        const result = await signUp(email, password)
        if (result.user) onAuthenticated(result.user, result.password_reset_email_enabled)
      } else if (mode === 'login') {
        const result = await signIn(email, password)
        if (result.user) onAuthenticated(result.user, result.password_reset_email_enabled)
      } else if (mode === 'forgot') {
        if (!passwordResetEmailEnabled) {
          setNotice('Password reset email is not configured yet. The administrator needs to add a Brevo API key and verified sender email.')
          return
        }
        const result = await requestPasswordReset(email)
        setNotice(result.message)
      } else if (resetToken) {
        await resetPassword(resetToken, password)
        setMode('login'); setPassword(''); setNotice('Password updated. Sign in with your new password.')
        window.history.replaceState({}, '', window.location.pathname)
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The account request failed.')
    } finally { setBusy(false) }
  }

  const switchMode = (next: Mode) => { setMode(next); setError(''); setNotice('') }

  return <div className="auth-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
      <button className="auth-close" onClick={onClose} aria-label="Close sign in">×</button>
      <div className="auth-kicker">V-SUTRA ACCOUNT</div>
      <h2 id="auth-title">{title}</h2>
      <p className="auth-description">Chat with text without an account. Sign in to unlock uploads, AI tools, voice and settings.</p>
      <form onSubmit={(event) => void submit(event)}>
        {mode !== 'reset' && <label>Email<input type="email" autoComplete="email" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} /></label>}
        {mode !== 'forgot' && <label>{mode === 'reset' ? 'New password' : 'Password'}<input type="password" autoComplete={mode === 'signup' || mode === 'reset' ? 'new-password' : 'current-password'} required minLength={mode === 'login' ? 1 : 12} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} />{mode !== 'login' && <small>Use at least 12 characters.</small>}</label>}
        {error && <p className="auth-error" role="alert">{error}</p>}
        {notice && <p className="auth-notice" role="status">{notice}</p>}
        <button className="auth-submit" type="submit" disabled={busy}>{busy ? 'Please wait…' : mode === 'signup' ? 'Create account' : mode === 'forgot' ? 'Send reset link' : mode === 'reset' ? 'Update password' : 'Sign in'}</button>
      </form>
      <div className="auth-links">
        {mode === 'login' && <><button onClick={() => switchMode('signup')}>Create account</button><button onClick={() => switchMode('forgot')}>Forgot password?</button></>}
        {mode === 'signup' && <button onClick={() => switchMode('login')}>Already have an account? Sign in</button>}
        {mode === 'forgot' && <button onClick={() => switchMode('login')}>Back to sign in</button>}
        {mode === 'reset' && <button onClick={() => switchMode('login')}>Back to sign in</button>}
      </div>
      <p className="auth-footnote">Your chat history stays in this browser and is not synced to your account.</p>
    </section>
  </div>
}
