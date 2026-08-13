import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, fmtDate, inr } from '../lib/api'
import type { Health, OrderSummary } from '../lib/types'
import { Countdown, Empty, ErrorBox, Loading, Page, Stat, Status } from '../components/ui'

const OPEN = new Set(['received', 'in_production', 'dispatched', 'invoiced', 'partially_paid'])

export default function Dashboard() {
  const orders = useQuery({
    queryKey: ['orders'],
    queryFn: () => api.get<OrderSummary[]>('/orders'),
  })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api.get<Health>('/health') })

  if (orders.isLoading) return <Loading what="orders" />
  if (orders.error) return <ErrorBox error={orders.error} />

  const rows = orders.data ?? []
  const open = rows.filter((row) => OPEN.has(row.status))
  const overdue = open.filter((row) => row.days_to_delivery !== null && row.days_to_delivery < 0)
  const dueSoon = open.filter(
    (row) => row.days_to_delivery !== null && row.days_to_delivery >= 0 && row.days_to_delivery <= 14,
  )

  const sum = (list: OrderSummary[], key: 'order_value' | 'unbilled' | 'received') =>
    list.reduce((total, row) => total + Number(row[key]), 0).toFixed(2)

  return (
    <Page
      title="Dashboard"
      subtitle="Open orders, delivery countdown and what is still to bill"
      actions={
        <Link className="btn-primary" to="/upload">
          Upload customer PO
        </Link>
      }
    >
      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Open orders" value={open.length} />
        <Stat label="Order value (open)" value={inr(sum(open, 'order_value'), true)} />
        <Stat label="Still to bill" value={inr(sum(open, 'unbilled'), true)} />
        <Stat
          label="Overdue deliveries"
          value={overdue.length}
          tone={overdue.length ? 'text-red-600' : ''}
        />
      </div>

      {dueSoon.length > 0 && (
        <div className="mb-5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>{dueSoon.length}</strong> order{dueSoon.length > 1 ? 's are' : ' is'} due within a
          fortnight: {dueSoon.map((row) => row.customer_po_number).join(', ')}
        </div>
      )}

      {rows.length === 0 ? (
        <Empty
          message="No orders yet"
          hint="Upload a customer PO to extract it and create the first order."
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px]">
              <thead>
                <tr>
                  <th className="th">Customer PO</th>
                  <th className="th">Customer</th>
                  <th className="th">Status</th>
                  <th className="th">Delivery</th>
                  <th className="th">Countdown</th>
                  <th className="th text-right">Order value</th>
                  <th className="th text-right">Billed</th>
                  <th className="th text-right">Unbilled</th>
                  <th className="th text-right">Received</th>
                  <th className="th">Next milestone</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-50">
                    <td className="td font-medium">
                      <Link className="text-brand-600 hover:underline" to={`/orders/${row.id}`}>
                        {row.customer_po_number}
                      </Link>
                    </td>
                    <td className="td">{row.customer_name}</td>
                    <td className="td"><Status value={row.status} /></td>
                    <td className="td tnum">{fmtDate(row.delivery_due_date)}</td>
                    <td className="td">
                      {OPEN.has(row.status) ? <Countdown days={row.days_to_delivery} /> : <span className="text-ink-mute">—</span>}
                    </td>
                    <td className="td tnum text-right">{inr(row.order_value)}</td>
                    <td className="td tnum text-right">{inr(row.billed)}</td>
                    <td className="td tnum text-right font-medium">{inr(row.unbilled)}</td>
                    <td className="td tnum text-right">{inr(row.received)}</td>
                    <td className="td text-ink-mute">{row.next_milestone ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {health.data && (
        <p className="mt-4 text-xs text-ink-mute">
          Extractor: <strong>{health.data.extractor_selected}</strong> ·{' '}
          {health.data.extractors
            .map((e) => `${e.name} ${e.available ? '✓' : '✗'}`)
            .join(' · ')}{' '}
          · database {health.data.database}
        </p>
      )}
    </Page>
  )
}
