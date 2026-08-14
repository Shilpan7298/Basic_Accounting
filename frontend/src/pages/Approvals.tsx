/**
 * The owner's approval queue.
 *
 * Everything the accountant changed on an already-issued document waits here.
 * Until it is approved the ledger still shows the previous version, so this
 * screen leads with the before/after rather than burying it.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, inr } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Empty, ErrorBox, Loading, Page } from '../components/ui'

interface PendingAmendment {
  id: string
  entity_type: string
  entity_id: string
  version_no: number
  status: string
  reason: string | null
  created_by: string
  created_at: string
  document_number: string | null
  document_type: string | null
  diff_json: {
    header: Record<string, { from: unknown; to: unknown }>
    lines: {
      line_no: number
      change: string
      fields?: Record<string, { from: unknown; to: unknown }>
      from?: Record<string, unknown>
      to?: Record<string, unknown>
    }[]
  } | null
}

const MONEY_FIELDS = new Set([
  'taxable_value', 'grand_total', 'unit_price', 'line_total', 'cgst_amount',
  'sgst_amount', 'igst_amount', 'round_off', 'qty',
])

function show(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '(empty)'
  if (MONEY_FIELDS.has(field)) return inr(String(value))
  return String(value)
}

function label(field: string): string {
  return field.replace(/_/g, ' ')
}

export default function Approvals() {
  const queryClient = useQueryClient()
  const { can } = useAuth()
  const [rejecting, setRejecting] = useState<string | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const pending = useQuery({
    queryKey: ['pending-amendments'],
    queryFn: () => api.get<PendingAmendment[]>('/amendments/pending'),
    refetchInterval: 30_000,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['pending-amendments'] })
    queryClient.invalidateQueries({ queryKey: ['invoices'] })
    queryClient.invalidateQueries({ queryKey: ['orders'] })
  }

  const approve = useMutation({
    mutationFn: (id: string) => api.post(`/amendments/${id}/approve`),
    onSuccess: invalidate,
  })

  const reject = useMutation({
    mutationFn: (vars: { id: string; reason: string }) =>
      api.post(`/amendments/${vars.id}/reject`, { reason: vars.reason }),
    onSuccess: () => {
      setRejecting(null)
      setRejectReason('')
      invalidate()
    },
  })

  if (pending.isLoading) return <Loading what="pending changes" />
  if (pending.error) return <ErrorBox error={pending.error} />

  const rows = pending.data ?? []

  return (
    <Page
      title="Pending changes"
      subtitle={
        can('document.amend.approve')
          ? 'Changes to issued documents wait here until you approve them. Until then the ledger still shows the previous version.'
          : 'Changes you have proposed. They take effect once the owner approves them.'
      }
    >
      {rows.length === 0 ? (
        <Empty
          message="Nothing waiting"
          hint="Amendments to already-issued documents will appear here for review."
        />
      ) : (
        <div className="space-y-4">
          {rows.map((row) => (
            <div key={row.id} className="card card-pad">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold tnum">{row.document_number ?? row.entity_id}</span>
                    <span className="chip bg-amber-50 text-amber-800">
                      version {row.version_no} pending
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-ink-soft">
                    <strong>{row.created_by}</strong> asks: {row.reason}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-mute">
                    proposed {new Date(row.created_at).toLocaleString('en-IN')}
                  </p>
                </div>
                {can('document.amend.approve') && (
                  <div className="flex gap-2">
                    <button
                      className="btn-ghost"
                      onClick={() => setRejecting(rejecting === row.id ? null : row.id)}
                    >
                      Reject
                    </button>
                    <button
                      className="btn-primary"
                      disabled={approve.isPending}
                      onClick={() => approve.mutate(row.id)}
                    >
                      Approve
                    </button>
                  </div>
                )}
              </div>

              {rejecting === row.id && (
                <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <input
                    className="input"
                    placeholder="Why are you rejecting this? (recorded in the audit trail)"
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                  />
                  <div className="mt-2 flex justify-end gap-2">
                    <button className="btn-ghost" onClick={() => setRejecting(null)}>
                      Cancel
                    </button>
                    <button
                      className="btn-danger"
                      disabled={rejectReason.trim().length < 3 || reject.isPending}
                      onClick={() => reject.mutate({ id: row.id, reason: rejectReason })}
                    >
                      Confirm rejection
                    </button>
                  </div>
                </div>
              )}

              {/* ---- what would change --------------------------------- */}
              {row.diff_json && (
                <div className="mt-4 space-y-3">
                  {Object.keys(row.diff_json.header).length > 0 && (
                    <div className="overflow-x-auto rounded-md border border-slate-200">
                      <table className="w-full min-w-[520px]">
                        <thead>
                          <tr>
                            <th className="th">Field</th>
                            <th className="th">Currently</th>
                            <th className="th">Would become</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(row.diff_json.header).map(([field, change]) => (
                            <tr key={field}>
                              <td className="td font-medium">{label(field)}</td>
                              <td className="td text-ink-mute line-through">
                                {show(field, change.from)}
                              </td>
                              <td className="td font-medium text-emerald-700">
                                {show(field, change.to)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {row.diff_json.lines.length > 0 && (
                    <div className="overflow-x-auto rounded-md border border-slate-200">
                      <table className="w-full min-w-[520px]">
                        <thead>
                          <tr>
                            <th className="th">Line</th>
                            <th className="th">Change</th>
                            <th className="th">Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {row.diff_json.lines.map((line, index) => (
                            <tr key={index}>
                              <td className="td tnum">{line.line_no}</td>
                              <td className="td">
                                <span
                                  className={`chip ${
                                    line.change === 'added'
                                      ? 'bg-emerald-50 text-emerald-700'
                                      : line.change === 'removed'
                                        ? 'bg-red-50 text-red-700'
                                        : 'bg-amber-50 text-amber-800'
                                  }`}
                                >
                                  {line.change}
                                </span>
                              </td>
                              <td className="td">
                                {line.fields
                                  ? Object.entries(line.fields).map(([field, change]) => (
                                      <div key={field} className="text-sm">
                                        {label(field)}:{' '}
                                        <span className="text-ink-mute line-through">
                                          {show(field, change.from)}
                                        </span>{' '}
                                        → <strong>{show(field, change.to)}</strong>
                                      </div>
                                    ))
                                  : (
                                      <span className="text-sm text-ink-mute">
                                        {String(
                                          (line.to ?? line.from)?.description ?? '',
                                        )}
                                      </span>
                                    )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="border-t border-slate-200 bg-slate-50 px-3 py-2 text-xs text-ink-mute">
                        GST has already been recalculated from the HSN codes and the
                        customer's state — approving applies exactly the figures shown.
                      </p>
                    </div>
                  )}
                </div>
              )}

              {approve.error && <div className="mt-3"><ErrorBox error={approve.error} /></div>}
              {reject.error && <div className="mt-3"><ErrorBox error={reject.error} /></div>}
            </div>
          ))}
        </div>
      )}
    </Page>
  )
}
