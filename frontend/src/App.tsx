import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './lib/api'
import { AuthProvider, P, useAuth } from './lib/auth'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Review from './pages/Review'
import OrderDetail from './pages/OrderDetail'
import { OfferDetail, PurchaseList } from './pages/Purchase'
import Tally from './pages/Tally'
import Reports from './pages/Reports'
import Masters from './pages/Masters'
import Login from './pages/Login'
import Approvals from './pages/Approvals'
import Users from './pages/Users'

const ROLE_LABEL: Record<string, string> = {
  owner: 'Owner — full access',
  accountant: 'Accountant — cannot change issued documents',
  viewer: 'Viewer — read only',
}

const NAV = [
  { to: '/', label: 'Dashboard', end: true, permission: null },
  { to: '/upload', label: 'Upload', permission: P.extractionRun },
  { to: '/purchase', label: 'Purchase', permission: P.reportRead },
  { to: '/approvals', label: 'Pending', permission: P.documentAmend, badge: true },
  { to: '/reports', label: 'Reports', permission: P.reportRead },
  { to: '/tally', label: 'Tally', permission: P.tallyExport },
  { to: '/masters', label: 'Masters', permission: P.masterEdit },
  { to: '/users', label: 'Users', permission: P.userManage },
]

function PendingBadge() {
  const { can } = useAuth()
  const pending = useQuery({
    queryKey: ['pending-amendments'],
    queryFn: () => api.get<unknown[]>('/amendments/pending'),
    refetchInterval: 60_000,
    enabled: can(P.documentAmend),
  })
  const count = pending.data?.length ?? 0
  if (!count) return null
  return (
    <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 text-[10px] font-bold text-white">
      {count}
    </span>
  )
}

function Shell() {
  const { user, isLoading, can, logout } = useAuth()

  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center text-sm text-ink-mute">
        Loading…
      </div>
    )
  }
  if (!user) return <Login />

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded bg-brand-600 text-xs font-bold text-white">
              U
            </span>
            <span className="text-sm font-semibold tracking-tight">Urjapod</span>
          </div>

          <nav className="flex flex-1 flex-wrap gap-1">
            {NAV.filter((item) => !item.permission || can(item.permission)).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `flex items-center rounded-md px-2.5 py-1.5 text-sm font-medium ${
                    isActive ? 'bg-brand-50 text-brand-700' : 'text-ink-soft hover:bg-slate-100'
                  }`
                }
              >
                {item.label}
                {item.badge && <PendingBadge />}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <div className="text-right">
              <div className="text-xs font-medium">{user.full_name}</div>
              <div className="text-[11px] text-ink-mute" title={ROLE_LABEL[user.role]}>
                {user.role}
              </div>
            </div>
            <button className="btn-ghost" onClick={() => logout()}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      {user.must_change_password && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-900">
          Your password was set by someone else. Please change it from{' '}
          <NavLink className="underline" to="/users">
            Users
          </NavLink>
          .
        </div>
      )}

      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review/:extractionId" element={<Review />} />
          <Route path="/orders/:orderId" element={<OrderDetail />} />
          <Route path="/purchase" element={<PurchaseList />} />
          <Route path="/purchase/offers/:offerId" element={<OfferDetail />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/tally" element={<Tally />} />
          <Route path="/masters" element={<Masters />} />
          <Route path="/users" element={<Users />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  )
}
