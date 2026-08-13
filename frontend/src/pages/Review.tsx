/**
 * The side-by-side review screen (M1).
 *
 * Left: the source PDF exactly as the customer sent it. Right: every extracted
 * field, editable, each with the model's confidence. Nothing financial exists
 * until Approve is pressed — that is the whole point of this screen, so the
 * button says so.
 */

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, inr } from '../lib/api'
import type { Extraction } from '../lib/types'
import { Confidence, ErrorBox, Field, Loading, Page, Status } from '../components/ui'

interface LineDraft {
  description: string
  hsn_code: string
  qty: string
  uom: string
  unit_price: string
  discount_percent: string
}

interface Draft {
  // customer PO
  customer_name?: string
  customer_gstin?: string
  customer_address?: string
  po_number?: string
  po_date?: string
  delivery_due_date?: string
  delivery_location?: string
  // supplier offer
  supplier_name?: string
  supplier_gstin?: string
  supplier_address?: string
  offer_ref?: string
  offer_date?: string
  validity_date?: string
  lead_time_days?: string
  freight_terms?: string
  warranty_text?: string
  // both
  payment_terms_text?: string
  notes?: string
  lines: LineDraft[]
}

const EMPTY_LINE: LineDraft = {
  description: '',
  hsn_code: '',
  qty: '1',
  uom: 'NOS',
  unit_price: '0',
  discount_percent: '0',
}

function toDraft(extraction: Extraction): Draft {
  const parsed = extraction.parsed_json ?? {}
  const lines = Array.isArray(parsed.lines) ? parsed.lines : []
  return {
    ...parsed,
    lines: lines.length
      ? lines.map((line: any) => ({
          description: line.description ?? '',
          hsn_code: line.hsn_code ?? '',
          qty: String(line.qty ?? '1'),
          uom: line.uom ?? 'NOS',
          unit_price: String(line.unit_price ?? '0'),
          discount_percent: String(line.discount_percent ?? '0'),
        }))
      : [{ ...EMPTY_LINE }],
  }
}

/** Line total, computed the same way the backend will: no float arithmetic
 *  is trusted here — this is a preview, the server recomputes on approve. */
function lineTotal(line: LineDraft): string {
  const qty = Number(line.qty || 0)
  const price = Number(line.unit_price || 0)
  const discount = Number(line.discount_percent || 0)
  const gross = qty * price
  return (gross - (gross * discount) / 100).toFixed(2)
}

export default function Review() {
  const { extractionId } = useParams<{ extractionId: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const documentId = params.get('document')

  const { data, isLoading, error } = useQuery({
    queryKey: ['extraction', extractionId],
    queryFn: () => api.get<Extraction>(`/extractions/${extractionId}`),
  })

  const [draft, setDraft] = useState<Draft | null>(null)
  useEffect(() => {
    if (data) setDraft(toDraft(data))
  }, [data])

  const isPO = data?.schema_name === 'customer_po'
  const confidence = data?.field_confidence_json ?? {}

  const approve = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { ...draft }
      // Empty strings must go back as nulls, not "" — a blank GSTIN is absent,
      // not invalid.
      for (const [key, value] of Object.entries(payload)) {
        if (value === '' ) payload[key] = null
      }
      payload.lines = (draft?.lines ?? [])
        .filter((line) => line.description.trim())
        .map((line) => ({
          description: line.description,
          hsn_code: line.hsn_code || null,
          qty: line.qty || '0',
          uom: line.uom || 'NOS',
          unit_price: line.unit_price || '0',
          discount_percent: line.discount_percent || '0',
        }))
      return api.post<any>(`/extractions/${extractionId}/approve`, { corrected: payload })
    },
    onSuccess: (result) => {
      if (result.kind === 'sales_order') navigate(`/orders/${result.sales_order.id}`)
      else navigate(`/purchase/offers/${result.supplier_offer.id}`)
    },
  })

  const total = useMemo(
    () =>
      (draft?.lines ?? [])
        .reduce((sum, line) => sum + Number(lineTotal(line)), 0)
        .toFixed(2),
    [draft],
  )

  if (isLoading) return <Loading what="the extraction" />
  if (error) return <ErrorBox error={error} />
  if (!data || !draft) return null

  const set = (key: keyof Draft, value: string) => setDraft({ ...draft, [key]: value })
  const setLine = (index: number, key: keyof LineDraft, value: string) => {
    const lines = [...draft.lines]
    lines[index] = { ...lines[index], [key]: value }
    setDraft({ ...draft, lines })
  }

  return (
    <Page
      title="Review extraction"
      subtitle={`${data.extractor_name}${data.model_name ? ` · ${data.model_name}` : ''} · nothing is saved until you approve`}
      actions={
        <>
          <Status value={data.status} />
          <Confidence value={data.overall_confidence} />
          <button
            className="btn-primary"
            disabled={approve.isPending || data.status === 'approved'}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? 'Saving…' : isPO ? 'Approve → create order' : 'Approve → create offer'}
          </button>
        </>
      }
    >
      {data.error_text && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <strong>Automatic extraction did not succeed.</strong> {data.error_text}
          <div className="mt-1 text-amber-800">
            Fill the fields in by hand below — the document is on the left.
          </div>
        </div>
      )}

      {!!data.warnings_json?.length && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <p className="text-sm font-semibold text-amber-900">Check these before approving</p>
          <ul className="mt-1 list-inside list-disc text-sm text-amber-800">
            {data.warnings_json.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {approve.error && <div className="mb-4"><ErrorBox error={approve.error} title="Could not approve" /></div>}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ---- source document ------------------------------------------ */}
        <div className="card overflow-hidden">
          <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
            Source document
          </div>
          {documentId ? (
            <object
              data={api.fileUrl(`/documents/${documentId}/file`)}
              type="application/pdf"
              className="h-[78vh] w-full"
            >
              <div className="p-4 text-sm text-ink-mute">
                Your browser will not display the PDF inline.{' '}
                <a
                  className="text-brand-600 underline"
                  href={api.fileUrl(`/documents/${documentId}/file`)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open it in a new tab
                </a>
                .
              </div>
            </object>
          ) : (
            <div className="p-4 text-sm text-ink-mute">No source document linked.</div>
          )}
        </div>

        {/* ---- extracted fields ----------------------------------------- */}
        <div className="card card-pad max-h-[78vh] overflow-y-auto">
          <div className="grid gap-3 sm:grid-cols-2">
            {isPO ? (
              <>
                <FieldRow label="Customer name" name="customer_name" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Customer GSTIN" name="customer_gstin" draft={draft} set={set} confidence={confidence}
                  hint="15 characters. Left blank if the checksum failed — retype it rather than guessing." />
                <FieldRow label="PO number" name="po_number" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="PO date" name="po_date" type="date" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Delivery due" name="delivery_due_date" type="date" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Delivery location" name="delivery_location" draft={draft} set={set} confidence={confidence} />
                <div className="sm:col-span-2">
                  <FieldRow label="Customer address" name="customer_address" textarea draft={draft} set={set} confidence={confidence} />
                </div>
              </>
            ) : (
              <>
                <FieldRow label="Supplier name" name="supplier_name" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Supplier GSTIN" name="supplier_gstin" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Offer reference" name="offer_ref" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Offer date" name="offer_date" type="date" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Valid until" name="validity_date" type="date" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Lead time (days)" name="lead_time_days" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Freight terms" name="freight_terms" draft={draft} set={set} confidence={confidence} />
                <FieldRow label="Warranty" name="warranty_text" draft={draft} set={set} confidence={confidence} />
                <div className="sm:col-span-2">
                  <FieldRow label="Supplier address" name="supplier_address" textarea draft={draft} set={set} confidence={confidence} />
                </div>
              </>
            )}
            <div className="sm:col-span-2">
              <FieldRow
                label="Payment terms (verbatim)"
                name="payment_terms_text"
                textarea
                draft={draft}
                set={set}
                confidence={confidence}
                hint="Copy exactly as written. The milestone parser reads this text — do not tidy it up."
              />
            </div>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold">Line items</h2>
              <button
                className="btn-ghost"
                onClick={() => setDraft({ ...draft, lines: [...draft.lines, { ...EMPTY_LINE }] })}
              >
                + Add line
              </button>
            </div>
            <div className="overflow-x-auto rounded-md border border-slate-200">
              <table className="w-full min-w-[640px]">
                <thead>
                  <tr>
                    <th className="th w-8">#</th>
                    <th className="th">Description</th>
                    <th className="th w-24">HSN</th>
                    <th className="th w-20">Qty</th>
                    <th className="th w-20">UOM</th>
                    <th className="th w-28">Rate</th>
                    <th className="th w-28 text-right">Amount</th>
                    <th className="th w-8" />
                  </tr>
                </thead>
                <tbody>
                  {draft.lines.map((line, index) => (
                    <tr key={index}>
                      <td className="td tnum text-ink-mute">{index + 1}</td>
                      <td className="td">
                        <input className="input" value={line.description}
                          onChange={(e) => setLine(index, 'description', e.target.value)} />
                      </td>
                      <td className="td">
                        <input className="input tnum" value={line.hsn_code} placeholder="8507"
                          onChange={(e) => setLine(index, 'hsn_code', e.target.value)} />
                      </td>
                      <td className="td">
                        <input className="input tnum" value={line.qty}
                          onChange={(e) => setLine(index, 'qty', e.target.value)} />
                      </td>
                      <td className="td">
                        <input className="input" value={line.uom}
                          onChange={(e) => setLine(index, 'uom', e.target.value)} />
                      </td>
                      <td className="td">
                        <input className="input tnum" value={line.unit_price}
                          onChange={(e) => setLine(index, 'unit_price', e.target.value)} />
                      </td>
                      <td className="td tnum text-right">{inr(lineTotal(line))}</td>
                      <td className="td">
                        <button
                          className="text-slate-400 hover:text-red-600"
                          title="Remove line"
                          onClick={() =>
                            setDraft({ ...draft, lines: draft.lines.filter((_, i) => i !== index) })
                          }
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td className="td font-semibold" colSpan={6}>
                      Total (before GST)
                    </td>
                    <td className="td tnum text-right font-semibold">{inr(total)}</td>
                    <td className="td" />
                  </tr>
                </tfoot>
              </table>
            </div>
            <p className="mt-2 text-xs text-ink-mute">
              GST is not shown here. It is computed from the HSN code and the customer's state when
              the tax invoice is raised — never typed in.
            </p>
          </div>
        </div>
      </div>
    </Page>
  )
}

function FieldRow({
  label,
  name,
  draft,
  set,
  confidence,
  type = 'text',
  textarea = false,
  hint,
}: {
  label: string
  name: keyof Draft
  draft: Draft
  set: (key: keyof Draft, value: string) => void
  confidence: Record<string, number>
  type?: string
  textarea?: boolean
  hint?: string
}) {
  const value = (draft[name] as string) ?? ''
  const score = confidence[name as string]
  return (
    <Field
      label={label}
      hint={hint}
    >
      <div className="flex items-center gap-2">
        {textarea ? (
          <textarea className="input" rows={3} value={value} onChange={(e) => set(name, e.target.value)} />
        ) : (
          <input
            className="input"
            type={type}
            value={type === 'date' ? String(value).slice(0, 10) : value}
            onChange={(e) => set(name, e.target.value)}
          />
        )}
        {score !== undefined && <Confidence value={score} />}
      </div>
    </Field>
  )
}
