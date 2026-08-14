/** User accounts and roles. Owner-only, except changing your own password. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { P, useAuth, type AuthUser } from '../lib/auth'
import { ErrorBox, Field, Loading, Page } from '../components/ui'

const ROLE_HELP: Record<string, string> = {
  owner:
    'Everything, including voiding and deleting entries and approving the accountant’s changes.',
  accountant:
    'Day-to-day entry. Can propose changes to issued documents but cannot make them take effect.',
  viewer: 'Read-only. Suitable for handing the CA access at year end.',
}

export default function Users() {
  const { user, can } = useAuth()
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    username: '',
    full_name: '',
    password: '',
    role: 'accountant',
  })
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' })
  const [pwMessage, setPwMessage] = useState<string | null>(null)

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<AuthUser[]>('/auth/users'),
    enabled: can(P.userManage),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  const createUser = useMutation({
    mutationFn: () => api.post<AuthUser>('/auth/users', { ...form, must_change_password: true }),
    onSuccess: () => {
      setForm({ username: '', full_name: '', password: '', role: 'accountant' })
      invalidate()
    },
  })

  const setRole = useMutation({
    mutationFn: (vars: { id: string; role: string }) =>
      api.post(`/auth/users/${vars.id}/role`, { role: vars.role }),
    onSuccess: invalidate,
  })

  const deactivate = useMutation({
    mutationFn: (id: string) => api.post(`/auth/users/${id}/deactivate`),
    onSuccess: invalidate,
  })

  const changePassword = useMutation({
    mutationFn: () =>
      api.post('/auth/change-password', {
        current_password: pw.current_password,
        new_password: pw.new_password,
      }),
    onSuccess: () => {
      setPw({ current_password: '', new_password: '', confirm: '' })
      setPwMessage('Password changed. You will need to sign in again on your other devices.')
    },
  })

  return (
    <Page title="Users" subtitle="Who can sign in, and what each of them may do">
      <div className="grid gap-4 lg:grid-cols-3">
        {/* ---- your own password ------------------------------------- */}
        <div className="card card-pad">
          <h2 className="mb-3 text-sm font-semibold">Change your password</h2>
          <div className="space-y-3">
            <Field label="Current password">
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                value={pw.current_password}
                onChange={(e) => setPw({ ...pw, current_password: e.target.value })}
              />
            </Field>
            <Field label="New password" hint="At least 10 characters.">
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={pw.new_password}
                onChange={(e) => setPw({ ...pw, new_password: e.target.value })}
              />
            </Field>
            <Field label="Repeat new password">
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={pw.confirm}
                onChange={(e) => setPw({ ...pw, confirm: e.target.value })}
              />
            </Field>
            {changePassword.error && <ErrorBox error={changePassword.error} />}
            {pwMessage && (
              <p className="rounded-md bg-emerald-50 p-2 text-sm text-emerald-800">{pwMessage}</p>
            )}
            <button
              className="btn-primary w-full"
              disabled={
                changePassword.isPending ||
                pw.new_password.length < 10 ||
                pw.new_password !== pw.confirm
              }
              onClick={() => changePassword.mutate()}
            >
              Change password
            </button>
          </div>
        </div>

        {/* ---- add a user (owner only) -------------------------------- */}
        {can(P.userManage) && (
          <div className="card card-pad">
            <h2 className="mb-3 text-sm font-semibold">Add a user</h2>
            <div className="space-y-3">
              <Field label="Username">
                <input
                  className="input"
                  value={form.username}
                  onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })}
                />
              </Field>
              <Field label="Full name">
                <input
                  className="input"
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                />
              </Field>
              <Field label="Role" hint={ROLE_HELP[form.role]}>
                <select
                  className="input"
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                >
                  <option value="accountant">Accountant</option>
                  <option value="viewer">Viewer</option>
                  <option value="owner">Owner</option>
                </select>
              </Field>
              <Field
                label="Temporary password"
                hint="They will be asked to change it after signing in."
              >
                <input
                  className="input"
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </Field>
              {createUser.error && <ErrorBox error={createUser.error} />}
              <button
                className="btn-primary w-full"
                disabled={
                  createUser.isPending ||
                  !form.username ||
                  !form.full_name ||
                  form.password.length < 10
                }
                onClick={() => createUser.mutate()}
              >
                Add user
              </button>
            </div>
          </div>
        )}

        {/* ---- the list ----------------------------------------------- */}
        {can(P.userManage) && (
          <div className="card overflow-hidden lg:col-span-1">
            {users.isLoading ? (
              <Loading what="users" />
            ) : (
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">User</th>
                    <th className="th">Role</th>
                    <th className="th" />
                  </tr>
                </thead>
                <tbody>
                  {(users.data ?? []).map((row) => (
                    <tr key={row.id} className={row.is_active ? '' : 'opacity-50'}>
                      <td className="td">
                        <div className="font-medium">{row.full_name}</div>
                        <div className="text-xs text-ink-mute">{row.username}</div>
                      </td>
                      <td className="td">
                        <select
                          className="input"
                          value={row.role}
                          disabled={!row.is_active}
                          onChange={(e) => setRole.mutate({ id: row.id, role: e.target.value })}
                        >
                          <option value="accountant">accountant</option>
                          <option value="viewer">viewer</option>
                          <option value="owner">owner</option>
                        </select>
                      </td>
                      <td className="td text-right">
                        {row.is_active && row.id !== user?.id && (
                          <button
                            className="btn-ghost"
                            onClick={() => deactivate.mutate(row.id)}
                          >
                            Disable
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {setRole.error && <div className="p-3"><ErrorBox error={setRole.error} /></div>}
            {deactivate.error && <div className="p-3"><ErrorBox error={deactivate.error} /></div>}
            <p className="border-t border-slate-200 bg-slate-50 px-3 py-2 text-xs text-ink-mute">
              Users are disabled, never deleted — their name appears on audit records that
              must remain readable.
            </p>
          </div>
        )}
      </div>
    </Page>
  )
}
