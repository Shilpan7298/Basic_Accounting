/**
 * Who is signed in, and what they may do.
 *
 * The permission list from the server drives which controls render. That is
 * for clarity, not security — every one of these actions is also refused by
 * the API. Hiding a button the user cannot use is courtesy; the API refusing
 * it is the actual protection.
 */

import { createContext, useCallback, useContext, useMemo } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'

export interface AuthUser {
  id: string
  username: string
  full_name: string
  email: string | null
  role: 'owner' | 'accountant' | 'viewer'
  is_active: boolean
  must_change_password: boolean
}

export interface Me {
  user: AuthUser
  permissions: string[]
}

interface AuthValue {
  user: AuthUser | null
  permissions: Set<string>
  can: (permission: string) => boolean
  isLoading: boolean
  setupRequired: boolean
  login: (username: string, password: string) => Promise<Me>
  bootstrap: (payload: BootstrapPayload) => Promise<Me>
  logout: () => Promise<void>
  loginError: unknown
}

export interface BootstrapPayload {
  username: string
  full_name: string
  password: string
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const setup = useQuery({
    queryKey: ['setup-required'],
    queryFn: () => api.get<{ setup_required: boolean }>('/auth/setup-required'),
    staleTime: Infinity,
  })

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/auth/me'),
    // A 401 here is the normal "not signed in" case, not an error to retry.
    retry: false,
    staleTime: 60_000,
  })

  const loginMutation = useMutation({
    mutationFn: (vars: { username: string; password: string }) =>
      api.post<Me>('/auth/login', vars),
    onSuccess: (data) => {
      queryClient.setQueryData(['me'], data)
      queryClient.invalidateQueries()
    },
  })

  const bootstrapMutation = useMutation({
    mutationFn: (payload: BootstrapPayload) =>
      api.post<Me>('/auth/bootstrap', { ...payload, role: 'owner' }),
    onSuccess: (data) => {
      queryClient.setQueryData(['me'], data)
      queryClient.invalidateQueries()
    },
  })

  const logoutMutation = useMutation({
    mutationFn: () => api.post('/auth/logout'),
    onSuccess: () => {
      queryClient.setQueryData(['me'], null)
      queryClient.clear()
    },
  })

  const permissions = useMemo(
    () => new Set(me.data?.permissions ?? []),
    [me.data?.permissions],
  )

  const can = useCallback((permission: string) => permissions.has(permission), [permissions])

  const value: AuthValue = {
    user: me.data?.user ?? null,
    permissions,
    can,
    isLoading: me.isLoading || setup.isLoading,
    setupRequired: setup.data?.setup_required ?? false,
    login: (username, password) => loginMutation.mutateAsync({ username, password }),
    bootstrap: (payload) => bootstrapMutation.mutateAsync(payload),
    logout: async () => {
      await logoutMutation.mutateAsync()
    },
    loginError: loginMutation.error ?? bootstrapMutation.error,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

/** Renders children only when the signed-in user holds the permission. */
export function Can({
  do: permission,
  children,
  otherwise = null,
}: {
  do: string
  children: ReactNode
  otherwise?: ReactNode
}) {
  const { can } = useAuth()
  return <>{can(permission) ? children : otherwise}</>
}

export const P = {
  documentCreate: 'document.create',
  documentIssue: 'document.issue',
  documentAmend: 'document.amend',
  documentAmendApprove: 'document.amend.approve',
  documentVoid: 'document.void',
  documentDelete: 'document.delete',
  masterEdit: 'master.edit',
  taxRateEdit: 'taxrate.edit',
  settingsEdit: 'settings.edit',
  userManage: 'user.manage',
  reportRead: 'report.read',
  auditRead: 'audit.read',
  tallyExport: 'tally.export',
  receiptRecord: 'receipt.record',
  orderTransition: 'order.transition',
  extractionRun: 'extraction.run',
} as const
