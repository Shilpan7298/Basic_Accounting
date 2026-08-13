import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api } from './lib/api'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Review from './pages/Review'
import OrderDetail from './pages/OrderDetail'
import { OfferDetail, PurchaseList } from './pages/Purchase'
import Tally from './pages/Tally'
import Reports from './pages/Reports'
import Masters from './pages/Masters'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/upload', label: 'Upload' },
  { to: '/purchase', label: 'Purchase' },
  { to: '/reports', label: 'Reports' },
  { to: '/tally', label: 'Tally' },
  { to: '/masters', label: 'Masters' },
]

export default function App() {
  // No auth provider by design (single company, few users) — but every audit
  // row still needs a name, so the operator identifies themselves here.
  const [actor, setActor] = useState(api.getActor())

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded bg-brand-600 text-xs font-bold text-white">
              U
            </span>
            <span className="text-sm font-semibold tracking-tight">Urjapod</span>
            <span className="hidden text-xs text-ink-mute sm:inline">Orders &amp; Invoicing</span>
          </div>

          <nav className="flex flex-1 flex-wrap gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1.5 text-sm font-medium ${
                    isActive ? 'bg-brand-50 text-brand-700' : 'text-ink-soft hover:bg-slate-100'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <label className="flex items-center gap-1.5 text-xs text-ink-mute">
            Signed in as
            <input
              className="w-28 rounded border border-slate-300 px-1.5 py-1 text-xs focus:border-brand-500 focus:outline-none"
              value={actor}
              title="Recorded against every change in the audit log"
              onChange={(e) => {
                setActor(e.target.value)
                api.setActor(e.target.value)
              }}
            />
          </label>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/review/:extractionId" element={<Review />} />
          <Route path="/orders/:orderId" element={<OrderDetail />} />
          <Route path="/purchase" element={<PurchaseList />} />
          <Route path="/purchase/offers/:offerId" element={<OfferDetail />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/tally" element={<Tally />} />
          <Route path="/masters" element={<Masters />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
