/** Purchase side: offers list, negotiation, and the Word purchase order. */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api, fmtDate, inr } from '../lib/api'
import type { Offer, PurchaseOrder } from '../lib/types'
import { Empty, ErrorBox, Field, Loading, Page, Stat, Status } from '../components/ui'

export function PurchaseList() {
  const offers = useQuery({ queryKey: ['offers'], queryFn: () => api.get<Offer[]>('/offers') })
  const pos = useQuery({
    queryKey: ['purchase-orders'],
    queryFn: () => api.get<PurchaseOrder[]>('/purchase-orders'),
  })

  if (offers.isLoading) return <Loading what="offers" />
  if (offers.error) return <ErrorBox error={offers.error} />

  return (
    <Page
      title="Purchase"
      subtitle="Supplier offers, negotiated rates and purchase orders"
      actions={
        <Link className="btn-primary" to="/upload">
          Upload supplier offer
        </Link>
      }
    >
      <h2 className="mb-2 text-sm font-semibold">Offers</h2>
      {offers.data?.length ? (
        <div className="card mb-6 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px]">
              <thead>
                <tr>
                  <th className="th">Reference</th>
                  <th className="th">Date</th>
                  <th className="th">Valid until</th>
                  <th className="th">Lead time</th>
                  <th className="th text-right">Lines</th>
                  <th className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {offers.data.map((offer) => (
                  <tr key={offer.id} className="hover:bg-slate-50">
                    <td className="td font-medium">
                      <Link className="text-brand-600 hover:underline" to={`/purchase/offers/${offer.id}`}>
                        {offer.offer_ref ?? '(no reference)'}
                      </Link>
                    </td>
                    <td className="td tnum">{fmtDate(offer.offer_date)}</td>
                    <td className="td tnum">{fmtDate(offer.validity_date)}</td>
                    <td className="td tnum">{offer.lead_time_days ? `${offer.lead_time_days}d` : '—'}</td>
                    <td className="td tnum text-right">{offer.lines.length}</td>
                    <td className="td"><Status value={offer.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="mb-6">
          <Empty message="No supplier offers yet" hint="Upload a quotation to extract and negotiate it." />
        </div>
      )}

      <h2 className="mb-2 text-sm font-semibold">Purchase orders</h2>
      {pos.data?.length ? (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead>
                <tr>
                  <th className="th">PO number</th>
                  <th className="th">Date</th>
                  <th className="th">Required by</th>
                  <th className="th text-right">Taxable</th>
                  <th className="th text-right">GST</th>
                  <th className="th text-right">Total</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Download</th>
                </tr>
              </thead>
              <tbody>
                {pos.data.map((po) => (
                  <tr key={po.id}>
                    <td className="td font-medium tnum">{po.number}</td>
                    <td className="td tnum">{fmtDate(po.po_date)}</td>
                    <td className="td tnum">{fmtDate(po.delivery_due_date)}</td>
                    <td className="td tnum text-right">{inr(po.taxable_value)}</td>
                    <td className="td tnum text-right">
                      {inr(
                        (
                          Number(po.cgst_amount) + Number(po.sgst_amount) + Number(po.igst_amount)
                        ).toFixed(2),
                      )}
                    </td>
                    <td className="td tnum text-right font-medium">{inr(po.grand_total)}</td>
                    <td className="td"><Status value={po.status} /></td>
                    <td className="td text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          className="btn-ghost"
                          disabled={!po.docx_path}
                          onClick={() => api.download(`/purchase-orders/${po.id}/download?fmt=docx`)}
                        >
                          Word
                        </button>
                        <button
                          className="btn-ghost"
                          disabled={!po.pdf_path}
                          onClick={() => api.download(`/purchase-orders/${po.id}/download?fmt=pdf`)}
                        >
                          PDF
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <Empty message="No purchase orders yet" />
      )}
    </Page>
  )
}

export function OfferDetail() {
  const { offerId } = useParams<{ offerId: string }>()
  const queryClient = useQueryClient()
  const [rates, setRates] = useState<Record<string, string>>({})
  const [deliveryDue, setDeliveryDue] = useState('')
  const [deliveryLocation, setDeliveryLocation] = useState('')

  const offer = useQuery({
    queryKey: ['offer', offerId],
    queryFn: () => api.get<Offer>(`/offers/${offerId}`),
  })

  useEffect(() => {
    if (offer.data) {
      setRates(
        Object.fromEntries(
          offer.data.lines.map((line) => [
            line.id,
            String(line.negotiated_unit_price ?? line.quoted_unit_price),
          ]),
        ),
      )
    }
  }, [offer.data])

  const negotiate = useMutation({
    mutationFn: () => api.post<Offer>(`/offers/${offerId}/negotiate`, { rates }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['offer', offerId] }),
  })

  const createPO = useMutation({
    mutationFn: () =>
      api.post<PurchaseOrder>(`/offers/${offerId}/purchase-order`, {
        delivery_due_date: deliveryDue || null,
        delivery_location: deliveryLocation || null,
        issue: true,
      }),
    onSuccess: (po) => {
      queryClient.invalidateQueries({ queryKey: ['offer', offerId] })
      queryClient.invalidateQueries({ queryKey: ['purchase-orders'] })
      api.download(`/purchase-orders/${po.id}/download?fmt=docx`)
    },
  })

  if (offer.isLoading) return <Loading what="the offer" />
  if (offer.error) return <ErrorBox error={offer.error} />
  if (!offer.data) return null

  const data = offer.data
  const quoted = data.lines.reduce(
    (sum, line) => sum + Number(line.qty) * Number(line.quoted_unit_price),
    0,
  )
  const negotiated = data.lines.reduce(
    (sum, line) => sum + Number(line.qty) * Number(rates[line.id] ?? line.quoted_unit_price),
    0,
  )
  const saving = quoted - negotiated

  return (
    <Page
      title={data.offer_ref ?? 'Supplier offer'}
      subtitle={`Offered ${fmtDate(data.offer_date)}${data.validity_date ? ` · valid to ${fmtDate(data.validity_date)}` : ''}`}
      actions={
        <>
          <Status value={data.status} />
          <button className="btn-ghost" disabled={negotiate.isPending} onClick={() => negotiate.mutate()}>
            {negotiate.isPending ? 'Saving…' : 'Save negotiated rates'}
          </button>
          <button
            className="btn-primary"
            disabled={createPO.isPending || data.status === 'ordered'}
            onClick={() => createPO.mutate()}
          >
            {createPO.isPending ? 'Creating…' : 'Create purchase order (Word)'}
          </button>
        </>
      }
    >
      {negotiate.error && <div className="mb-4"><ErrorBox error={negotiate.error} /></div>}
      {createPO.error && <div className="mb-4"><ErrorBox error={createPO.error} title="Could not create the PO" /></div>}

      <div className="mb-5 grid gap-3 sm:grid-cols-3">
        <Stat label="Quoted (basic)" value={inr(quoted.toFixed(2), true)} />
        <Stat label="Negotiated (basic)" value={inr(negotiated.toFixed(2), true)} />
        <Stat
          label="Saving"
          value={inr(saving.toFixed(2), true)}
          tone={saving > 0 ? 'text-emerald-600' : saving < 0 ? 'text-red-600' : ''}
        />
      </div>

      <div className="card mb-5 overflow-hidden">
        <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
          Rates — quoted beside negotiated
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px]">
            <thead>
              <tr>
                <th className="th w-10">#</th>
                <th className="th">Description</th>
                <th className="th">HSN</th>
                <th className="th text-right">Qty</th>
                <th className="th text-right">Quoted rate</th>
                <th className="th w-36 text-right">Negotiated rate</th>
                <th className="th text-right">Line total</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((line) => {
                const rate = rates[line.id] ?? String(line.quoted_unit_price)
                const total = (Number(line.qty) * Number(rate)).toFixed(2)
                const cheaper = Number(rate) < Number(line.quoted_unit_price)
                return (
                  <tr key={line.id}>
                    <td className="td tnum">{line.line_no}</td>
                    <td className="td">{line.description}</td>
                    <td className="td tnum">{line.hsn_code ?? '—'}</td>
                    <td className="td tnum text-right">{inr(line.qty)}</td>
                    <td className="td tnum text-right text-ink-mute">{inr(line.quoted_unit_price)}</td>
                    <td className="td">
                      <input
                        className={`input tnum text-right ${cheaper ? 'border-emerald-400' : ''}`}
                        value={rate}
                        onChange={(e) => setRates({ ...rates, [line.id]: e.target.value })}
                      />
                    </td>
                    <td className="td tnum text-right font-medium">{inr(total)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card card-pad">
        <h2 className="mb-3 text-sm font-semibold">Purchase order details</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Required by">
            <input
              className="input"
              type="date"
              value={deliveryDue}
              onChange={(e) => setDeliveryDue(e.target.value)}
            />
          </Field>
          <Field label="Deliver to" hint="Leave blank to use the company address.">
            <input
              className="input"
              value={deliveryLocation}
              onChange={(e) => setDeliveryLocation(e.target.value)}
            />
          </Field>
        </div>
        <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="label">Payment terms</dt>
            <dd>{data.payment_terms_text ?? '—'}</dd>
          </div>
          <div>
            <dt className="label">Freight</dt>
            <dd>{data.freight_terms ?? '—'}</dd>
          </div>
          <div>
            <dt className="label">Warranty</dt>
            <dd>{data.warranty_text ?? '—'}</dd>
          </div>
        </dl>
        <p className="mt-3 text-xs text-ink-mute">
          GST on the PO is computed from each line's HSN and the supplier's state — an out-of-state
          supplier charges IGST, a Gujarat supplier charges CGST + SGST. It is never typed in.
        </p>
      </div>
    </Page>
  )
}
