import { useState } from 'react'
import { useAuth } from '../lib/auth'
import { ErrorBox, Field } from '../components/ui'

export default function Login() {
  const { setupRequired, login, bootstrap, loginError } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const first = setupRequired

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setLocalError(null)
    if (first && password !== confirm) {
      setLocalError('The two passwords do not match.')
      return
    }
    if (first && password.length < 10) {
      setLocalError('Choose a password of at least 10 characters.')
      return
    }
    setBusy(true)
    try {
      if (first) await bootstrap({ username, full_name: fullName, password })
      else await login(username, password)
    } catch {
      /* surfaced through loginError */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-100 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-brand-600 text-lg font-bold text-white">
            U
          </div>
          <h1 className="mt-3 text-lg font-semibold tracking-tight">
            Urjapod Orders &amp; Invoicing
          </h1>
          <p className="mt-1 text-sm text-ink-mute">
            {first ? 'Set up the owner account' : 'Sign in to continue'}
          </p>
        </div>

        <form className="card card-pad space-y-4" onSubmit={submit}>
          {first && (
            <div className="rounded-md border border-brand-100 bg-brand-50 p-3 text-sm text-brand-700">
              This is the first run. The account you create now is the{' '}
              <strong>owner</strong> — the only role that can void or delete entries and
              approve the accountant's changes. Keep these details safe.
            </div>
          )}

          <Field label="Username">
            <input
              className="input"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
            />
          </Field>

          {first && (
            <Field label="Full name">
              <input
                className="input"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
              />
            </Field>
          )}

          <Field
            label="Password"
            hint={first ? 'At least 10 characters. Length matters more than symbols.' : undefined}
          >
            <input
              className="input"
              type="password"
              autoComplete={first ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {first && (
            <Field label="Repeat password">
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </Field>
          )}

          {localError ? <ErrorBox error={new Error(localError)} title="Check this" /> : null}
          {loginError && !localError ? (
            <ErrorBox
              error={loginError}
              title={first ? 'Could not create the account' : 'Sign in failed'}
            />
          ) : null}

          <button
            className="btn-primary w-full"
            disabled={busy || !username || !password || (first && !fullName)}
          >
            {busy ? 'Please wait…' : first ? 'Create owner account' : 'Sign in'}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-ink-mute">
          Every change you make is recorded against your name and cannot be erased.
        </p>
      </div>
    </div>
  )
}
