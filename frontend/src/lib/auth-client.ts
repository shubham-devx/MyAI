const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? (process.env.NODE_ENV === 'production' ? '/backend-api' : 'http://localhost:8000')

export type AuthUser = { id: string; email: string }
type AuthResult = { user: AuthUser | null; password_reset_email_enabled: boolean }

async function authRequest<T>(path: string, body?: Record<string, string>): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: body ? 'POST' : 'GET',
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  let result: Record<string, unknown> = {}
  try { result = await response.json() as Record<string, unknown> } catch { /* Use the safe fallback below. */ }
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'The account request failed.')
  return result as T
}

export const getCurrentUser = () => authRequest<AuthResult>('/api/auth/me')
export const signUp = (email: string, password: string) => authRequest<AuthResult>('/api/auth/signup', { email, password })
export const signIn = (email: string, password: string) => authRequest<AuthResult>('/api/auth/login', { email, password })
export const signOut = () => authRequest<{ logged_out: boolean }>('/api/auth/logout', {})
export const requestPasswordReset = (email: string) => authRequest<{ message: string }>('/api/auth/forgot-password', { email })
export const resetPassword = (token: string, password: string) => authRequest<{ password_reset: boolean }>('/api/auth/reset-password', { token, password })
