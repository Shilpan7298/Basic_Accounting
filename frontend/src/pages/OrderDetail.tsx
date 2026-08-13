/**
 * One order: its lines, its milestone schedule, and the documents raised
 * against it. Includes the manual schedule builder, which the parser falls
 * back to whenever it is not certain — so it is a first-class screen, not a
 * hidden escape hatch.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, fmtDate, inr, pct } from '../lib/api'
import type { BuildMilestonesResult, Invoice, Milestone, Order } from '../lib/types'
import { ErrorBox, Loading, Page, Stat, Status } from '../components/ui'

const NEXT_STATUS: Record<string, string[]> = {
  draft: ['received', 'cancelled'],
  received: ['in_production', 'cancelled'],
  in_production: ['dispatched', 'cancelled'],
  dispatched: ['invoiced', 'cancelled'],
  invoiced: ['partially_paid', 'paid', 'cancelled'],
  partially_paid: ['paid', 'cancelled'],
  paid: [],
  cancelled: [],
}

const TRIGGERS = [
  'ON_PO',
  'BEFORE_DISPATCH',
  'ON_DELIVERY',
  'AFTER_COMMISSIONING',
  'NET_DAYS',
  'MANUAL',
]

interface ManualRow {
  label: string
  trigger_event: string
  percent: string
  net_days: string
}

export default function OrderDetail() {
  const { orderId } = useParams<{ orderId: string }>()
  const queryClient = useQueryClient()
  const [builder, setBuilder] = useState<ManualRow[] | null>(null)
  const [parseResult, setParseResult] = useState<BuildMilestonesResult | null>(null)

  const order = useQuery({
    queryKey: ['order', orderId],
    queryFn: () => api.get<Order>(`/orders/${orderId}`),
  })
  const invoices = useQuery({
    queryKey: ['invoices', orderId],
    queryFn: () => api.get<Invoice[]>(`/invoices?order_id=${orderId}`),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['order', orderId] })
    queryClient.invalidateQueries({ queryKey: ['invoices', orderId] })
    queryClient.invalidateQueries({ queryKey: ['orders'] })
  }

  const buildFromTerms = useMutation({
    mutationFn: () => api.post<BuildMilestonesResult>(`/orders/${orderId}/milestones`, {}),
    onSuccess: (result) => {
      setParseResult(result)
      if (result.needs_manual_schedule) {
        // Seed the builder with whatever the parser did manage to read, so the
        // human corrects rather than retypes.
        setBuilder(
          (result.parsed?.milestones ?? []).map((m) => ({
            label: m.label,
            trigger_event: m.trigger_event,
            percent: String(m.percent),
            net_days: m.net_days ? String(m.net_days) : '',
          })) || [],
        )
      } else {
        setBuilder(null)
      }
      invalidate()
    },
  })

  const saveManual = useMutation({
    mutationFn: () =>
      api.post<BuildMilestonesResult>(`/orders/${orderId}/milestones`, {
        manual: (builder ?? []).map((row) => ({
          label: row.label,
          trigger_event: row.trigger_event,
          percent: row.percent,
          net_days: row.net_days ? Number(row.net_days) : null,
        })),
      }),
    onSuccess: () => {
      setBuilder(null)
      setParseResult(null)
      invalidate()
    },
  })

  const raiseProforma = useMutation({
    mutationFn: (milestoneId: string) =>
      api.post<Invoice>(`/orders/${orderId}/proforma`, { milestone_id: milestoneId, issue: true }),
    onSuccess: invalidate,
  })

  const raiseTaxInvoice = useMutation({
    mutationFn: () => api.post<Invoice>(`/orders/${orderId}/tax-invoice`, { issue: true }),
    onSuccess: invalidate,
  })

  const transition = useMutation({
    mutationFn: (target: string) => api.post(`/orders/${orderId}/transition`, { target }),
    onSuccess: invalidate,
  })

  if (order.isLoading) return <Loading what="the order" />
  if (order.error) return <ErrorBox error={order.error} />
  if (!order.data) return null

  const data = order.data
  const milestones = data.milestones ?? []
  const builderTotal = (builder ?? []).reduce((sum, row) => sum + Number(row.percent || 0), 0)

  return (
    <Page
      title={data.customer_po_number}
      subtitle={`PO dated ${fmtDate(data.customer_po_date)} · delivery ${fmtDate(data.delivery_due_date)}`}
      actions={
        <>
          <Status value={data.status} />
          {NEXT_STATUS[data.status]?.map((target) => (
            <button
              key={target}
              className={target === 'cancelled' ? 'btn-danger' : 'btn-ghost'}
              disabled={transition.isPending}
              onClick={() => transition.mutate(target)}
            >
              → {target.replace(/_/g, ' ')}
            </button>
          ))}
          <button
            className="btn-primary"
            disabled={raiseTaxInvoice.isPending}
            onClick={() => raiseTaxInvoice.mutate()}
          >
            {raiseTaxInvoice.isPending ? 'Raising…' : 'Raise tax invoice'}
          </button>
        </>
      }
    >
      {transition.error && <div className="mb-4"><ErrorBox error={transition.error} title="Cannot change status" /></div>}
      {raiseTaxInvoice.error && (
        <div className="mb-4"><ErrorBox error={raiseTaxInvoice.error} title="Cannot raise the tax invoice" /></div>
      )}

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Order value" value={inr(data.order_value, true)} />
        <Stat label="Milestones" value={milestones.length || '—'} />
        <Stat
          label="Billed"
          value={inr(
            milestones
              .filter((m) => m.status !== 'pending')
              .reduce((sum, m) => sum + Number(m.amount), 0)
              .toFixed(2),
          )}
        />
        <Stat label="Documents" value={invoices.data?.length ?? 0} />
      </div>

      {/* ---- payment terms and milestones ------------------------------- */}
      <div className="card card-pad mb-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="max-w-2xl">
            <h2 className="text-sm font-semibold">Payment terms</h2>
            <p className="mt-1 text-sm text-ink-soft">
              {data.payment_terms_text || <span className="text-ink-mute">Not recorded on the PO.</span>}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-ghost"
              disabled={buildFromTerms.isPending}
              onClick={() => buildFromTerms.mutate()}
            >
              {milestones.length ? 'Re-parse terms' : 'Parse into milestones'}
            </button>
            <button
              className="btn-ghost"
              onClick={() =>
                setBuilder(
                  milestones.length
                    ? milestones.map((m) => ({
                        label: m.label,
                        trigger_event: m.trigger_event,
                        percent: String(m.percent),
                        net_days: m.net_days ? String(m.net_days) : '',
                      }))
                    : [{ label: 'Advance along with PO', trigger_event: 'ON_PO', percent: '30', net_days: '' }],
                )
              }
            >
              Build manually
            </button>
          </div>
        </div>

        {parseResult?.parsed && (
          <div
            className={`mt-3 rounded-md border p-3 text-sm ${
              parseResult.needs_manual_schedule
                ? 'border-amber-200 bg-amber-50 text-amber-900'
                : 'border-emerald-200 bg-emerald-50 text-emerald-900'
            }`}
          >
            <p>
              Parsed at <strong>{Math.round(parseResult.parsed.confidence * 100)}%</strong> confidence,
              totalling <strong>{pct(parseResult.parsed.total_percent)}</strong>.
              {parseResult.needs_manual_schedule &&
                ' It does not resolve to exactly 100%, so nothing was saved — build the schedule by hand below.'}
            </p>
            {parseResult.parsed.warnings.length > 0 && (
              <ul className="mt-1 list-inside list-disc">
                {parseResult.parsed.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {builder && (
          <div className="mt-4 rounded-md border border-slate-200 p-3">
            <h3 className="text-sm font-semibold">Manual schedule builder</h3>
            <div className="mt-2 space-y-2">
              {builder.map((row, index) => (
                <div key={index} className="grid gap-2 sm:grid-cols-12">
                  <div className="sm:col-span-5">
                    <input
                      className="input"
                      placeholder="Label, e.g. Advance along with PO"
                      value={row.label}
                      onChange={(e) => {
                        const next = [...builder]
                        next[index] = { ...row, label: e.target.value }
                        setBuilder(next)
                      }}
                    />
                  </div>
                  <div className="sm:col-span-3">
                    <select
                      className="input"
                      value={row.trigger_event}
                      onChange={(e) => {
                        const next = [...builder]
                        next[index] = { ...row, trigger_event: e.target.value }
                        setBuilder(next)
                      }}
                    >
                      {TRIGGERS.map((trigger) => (
                        <option key={trigger} value={trigger}>
                          {trigger.replace(/_/g, ' ').toLowerCase()}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="sm:col-span-2">
                    <input
                      className="input tnum"
                      placeholder="%"
                      value={row.percent}
                      onChange={(e) => {
                        const next = [...builder]
                        next[index] = { ...row, percent: e.target.value }
                        setBuilder(next)
                      }}
                    />
                  </div>
                  <div className="sm:col-span-1">
                    <input
                      className="input tnum"
                      placeholder="days"
                      value={row.net_days}
                      onChange={(e) => {
                        const next = [...builder]
                        next[index] = { ...row, net_days: e.target.value }
                        setBuilder(next)
                      }}
                    />
                  </div>
                  <div className="sm:col-span-1">
                    <button
                      className="btn-ghost w-full"
                      onClick={() => setBuilder(builder.filter((_, i) => i !== index))}
                    >
                      ×
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-3">
                <button
                  className="btn-ghost"
                  onClick={() =>
                    setBuilder([
                      ...builder,
                      { label: '', trigger_event: 'BEFORE_DISPATCH', percent: '', net_days: '' },
                    ])
                  }
                >
                  + Add milestone
                </button>
                <span
                  className={`text-sm tnum ${
                    builderTotal === 100 ? 'text-emerald-700' : 'text-red-700'
                  }`}
                >
                  Total {builderTotal.toFixed(2)}% {builderTotal === 100 ? '✓' : '— must be exactly 100%'}
                </span>
              </div>
              <div className="flex gap-2">
                <button className="btn-ghost" onClick={() => setBuilder(null)}>
                  Cancel
                </button>
                <button
                  className="btn-primary"
                  disabled={builderTotal !== 100 || saveManual.isPending}
                  onClick={() => saveManual.mutate()}
                >
                  Save schedule
                </button>
              </div>
            </div>
            {saveManual.error && (
              <div className="mt-3"><ErrorBox error={saveManual.error} title="Could not save" /></div>
            )}
          </div>
        )}

        {milestones.length > 0 && (
          <div className="mt-4 overflow-x-auto rounded-md border border-slate-200">
            <table className="w-full min-w-[720px]">
              <thead>
                <tr>
                  <th className="th w-10">#</th>
                  <th className="th">Milestone</th>
                  <th className="th">Trigger</th>
                  <th className="th text-right">%</th>
                  <th className="th text-right">Amount</th>
                  <th className="th">Due</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {milestones.map((milestone: Milestone) => (
                  <tr key={milestone.id}>
                    <td className="td tnum">{milestone.seq}</td>
                    <td className="td">{milestone.label}</td>
                    <td className="td text-ink-mute">
                      {milestone.trigger_event.replace(/_/g, ' ').toLowerCase()}
                    </td>
                    <td className="td tnum text-right">{pct(milestone.percent)}</td>
                    <td className="td tnum text-right">{inr(milestone.amount)}</td>
                    <td className="td tnum">{fmtDate(milestone.due_date)}</td>
                    <td className="td"><Status value={milestone.status} /></td>
                    <td className="td text-right">
                      {milestone.status === 'pending' ? (
                        <button
                          className="btn-ghost"
                          disabled={raiseProforma.isPending}
                          onClick={() => raiseProforma.mutate(milestone.id)}
                        >
                          Raise proforma
                        </button>
                      ) : (
                        <span className="text-xs text-ink-mute">billed</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {raiseProforma.error && (
          <div className="mt-3"><ErrorBox error={raiseProforma.error} title="Could not raise the proforma" /></div>
        )}
      </div>

      {/* ---- lines ------------------------------------------------------- */}
      <div className="card mb-5 overflow-hidden">
        <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
          Order lines
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr>
                <th className="th w-10">#</th>
                <th className="th">Description</th>
                <th className="th">HSN</th>
                <th className="th text-right">Qty</th>
                <th className="th">UOM</th>
                <th className="th text-right">Rate</th>
                <th className="th text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={line.id}>
                  <td className="td tnum">{line.line_no}</td>
                  <td className="td">{line.description}</td>
                  <td className="td tnum">{line.hsn_code ?? <span className="text-red-600">missing</span>}</td>
                  <td className="td tnum text-right">{inr(line.qty)}</td>
                  <td className="td">{line.uom}</td>
                  <td className="td tnum text-right">{inr(line.unit_price)}</td>
                  <td className="td tnum text-right">{inr(line.line_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ---- documents --------------------------------------------------- */}
      <div className="card overflow-hidden">
        <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
          Documents raised
        </div>
        {invoices.data?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead>
                <tr>
                  <th className="th">Number</th>
                  <th className="th">Type</th>
                  <th className="th">Date</th>
                  <th className="th text-right">Taxable</th>
                  <th className="th text-right">GST</th>
                  <th className="th text-right">Total</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Download</th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.map((invoice) => (
                  <tr key={invoice.id}>
                    <td className="td font-medium tnum">{invoice.number}</td>
                    <td className="td">
                      <span
                        className={`chip ${
                          invoice.doc_type === 'PROFORMA'
                            ? 'bg-amber-50 text-amber-800'
                            : 'bg-indigo-50 text-indigo-700'
                        }`}
                      >
                        {invoice.doc_type === 'PROFORMA' ? 'proforma' : 'tax invoice'}
                      </span>
                    </td>
                    <td className="td tnum">{fmtDate(invoice.invoice_date)}</td>
                    <td className="td tnum text-right">{inr(invoice.taxable_value)}</td>
                    <td className="td tnum text-right">
                      {inr(
                        (
                          Number(invoice.cgst_amount) +
                          Number(invoice.sgst_amount) +
                          Number(invoice.igst_amount)
                        ).toFixed(2),
                      )}
                    </td>
                    <td className="td tnum text-right font-medium">{inr(invoice.grand_total)}</td>
                    <td className="td"><Status value={invoice.status} /></td>
                    <td className="td text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          className="btn-ghost"
                          disabled={!invoice.pdf_path}
                          onClick={() => api.download(`/invoices/${invoice.id}/download?fmt=pdf`)}
                        >
                          PDF
                        </button>
                        <button
                          className="btn-ghost"
                          disabled={!invoice.docx_path}
                          onClick={() => api.download(`/invoices/${invoice.id}/download?fmt=docx`)}
                        >
                          Word
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-4 text-sm text-ink-mute">
            Nothing raised yet. Parse the payment terms, then raise the first proforma.
          </div>
        )}
      </div>
    </Page>
  )
}
