/**
 * What you can do to an issued document, filtered by your role.
 *
 * The accountant sees Amend (which proposes) and the downloads. The owner also
 * sees Void and, behind an extra confirmation, Delete. Every action asks for a
 * reason, because the reason is what the audit trail is actually for.
 */

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api, fmtDate, inr } from '../lib/api'
import { P, useAuth } from '../lib/auth'
import type { Invoice } from '../lib/types'
import { ErrorBox, Field } from './ui'

type Mode = null | 'amend' | 'void' | 'delete' | 'history'

interface Version {
  id: string
  version_no: number
  status: string
  reason: string | null
  created_by: string
  created_at: string
  approved_by: string | null
}

export default function DocumentActions({
  invoice,
  onChanged,
}: {
  invoice: Invoice
  onChanged: () => void
}) {
  const { can } = useAuth()
  const [mode, setMode] = useState<Mode>(null)
  const [reason, setReason] = useState('')
  const [notes, setNotes] = useState(invoice.notes ?? '')
  const [confirmNumber, setConfirmNumber] = useState('')

  const close = () => {
    setMode(null)
    setReason('')
    setConfirmNumber('')
  }

  const history = useQuery({
    queryKey: ['versions', invoice.id],
    queryFn: () => api.get<Version[]>(`/invoices/${invoice.id}/versions`),
    enabled: mode === 'history',
  })

  const amend = useMutation({
    mutationFn: () =>
      api.post(`/invoices/${invoice.id}/amend`, {
        reason,
        header: { notes },
      }),
    onSuccess: () => {
      close()
      onChanged()
    },
  })

  const voidIt = useMutation({
    mutationFn: () => api.post(`/invoices/${invoice.id}/void`, { reason }),
    onSuccess: () => {
      close()
      onChanged()
    },
  })

  const remove = useMutation({
    mutationFn: () =>
      api.del(
        `/invoices/${invoice.id}?reason=${encodeURIComponent(reason)}&confirm=${encodeURIComponent(confirmNumber)}`,
      ),
    onSuccess: () => {
      close()
      onChanged()
    },
  })

  const issued = invoice.status === 'issued'

  return (
    <>
      <div className="flex flex-wrap justify-end gap-1">
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
        <button className="btn-ghost" onClick={() => setMode('history')}>
          History
        </button>
        {issued && can(P.documentAmend) && (
          <button className="btn-ghost" onClick={() => setMode('amend')}>
            Amend
          </button>
        )}
        {issued && can(P.documentVoid) && (
          <button className="btn-danger" onClick={() => setMode('void')}>
            Void
          </button>
        )}
        {can(P.documentDelete) && (
          <button
            className="btn-ghost text-red-600"
            title="Permanently delete — leaves a gap in the invoice series"
            onClick={() => setMode('delete')}
          >
            Delete
          </button>
        )}
      </div>

      {mode && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-lg bg-white p-5 text-left shadow-xl">
            {/* ---- amend ------------------------------------------- */}
            {mode === 'amend' && (
              <>
                <h2 className="text-base font-semibold">Amend {invoice.number}</h2>
                <p className="mt-1 text-sm text-ink-mute">
                  {can(P.documentAmendApprove)
                    ? 'This creates a new version and applies immediately. The current version is kept.'
                    : 'This creates a new version and waits for the owner to approve it. Until then the invoice stays as it is.'}
                </p>
                <div className="mt-4 space-y-3">
                  <Field label="Notes on the invoice">
                    <input
                      className="input"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="e.g. Customer ref: GTL/2025/889"
                    />
                  </Field>
                  <Field label="Reason for the change" hint="Recorded in the audit trail.">
                    <input
                      className="input"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                  {amend.error && <ErrorBox error={amend.error} />}
                </div>
                <div className="mt-5 flex justify-end gap-2">
                  <button className="btn-ghost" onClick={close}>Cancel</button>
                  <button
                    className="btn-primary"
                    disabled={reason.trim().length < 3 || amend.isPending}
                    onClick={() => amend.mutate()}
                  >
                    {can(P.documentAmendApprove) ? 'Apply amendment' : 'Submit for approval'}
                  </button>
                </div>
              </>
            )}

            {/* ---- void -------------------------------------------- */}
            {mode === 'void' && (
              <>
                <h2 className="text-base font-semibold">Void {invoice.number}</h2>
                <p className="mt-1 text-sm text-ink-mute">
                  The invoice drops out of the ledger, the reports and the Tally export, but
                  keeps its number so the series stays gapless. This is the right choice in
                  almost every case.
                </p>
                <div className="mt-4">
                  <Field label="Reason">
                    <input
                      className="input"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      placeholder="e.g. raised against the wrong customer"
                    />
                  </Field>
                  {voidIt.error && <div className="mt-3"><ErrorBox error={voidIt.error} /></div>}
                </div>
                <div className="mt-5 flex justify-end gap-2">
                  <button className="btn-ghost" onClick={close}>Cancel</button>
                  <button
                    className="btn-danger"
                    disabled={reason.trim().length < 3 || voidIt.isPending}
                    onClick={() => voidIt.mutate()}
                  >
                    Void invoice
                  </button>
                </div>
              </>
            )}

            {/* ---- delete ------------------------------------------ */}
            {mode === 'delete' && (
              <>
                <h2 className="text-base font-semibold text-red-700">
                  Permanently delete {invoice.number}
                </h2>
                <div className="mt-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
                  <p>
                    <strong>This removes the invoice from the books entirely</strong> and leaves a
                    gap in the numbering — which an auditor will ask about.
                  </p>
                  <p className="mt-2">
                    <strong>Void</strong> does everything you probably want and keeps the record.
                    Only delete something that should never have existed, such as a duplicate
                    created during setup.
                  </p>
                  <p className="mt-2">
                    A complete copy of this invoice is written to the audit log first, so what
                    was deleted stays recoverable.
                  </p>
                </div>
                <div className="mt-4 space-y-3">
                  <Field label="Reason">
                    <input
                      className="input"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                  <Field
                    label={`Type ${invoice.number} to confirm`}
                    hint="Deliberately awkward."
                  >
                    <input
                      className="input tnum"
                      value={confirmNumber}
                      onChange={(e) => setConfirmNumber(e.target.value)}
                    />
                  </Field>
                  {remove.error && <ErrorBox error={remove.error} />}
                </div>
                <div className="mt-5 flex justify-end gap-2">
                  <button className="btn-ghost" onClick={close}>Cancel</button>
                  <button
                    className="btn-danger"
                    disabled={
                      reason.trim().length < 3 ||
                      confirmNumber !== invoice.number ||
                      remove.isPending
                    }
                    onClick={() => remove.mutate()}
                  >
                    Delete permanently
                  </button>
                </div>
              </>
            )}

            {/* ---- history ----------------------------------------- */}
            {mode === 'history' && (
              <>
                <h2 className="text-base font-semibold">History of {invoice.number}</h2>
                <p className="mt-1 text-sm text-ink-mute">
                  Every version is kept. Nothing here is ever overwritten.
                </p>
                <div className="mt-4 max-h-80 overflow-y-auto rounded-md border border-slate-200">
                  <table className="w-full">
                    <thead>
                      <tr>
                        <th className="th">Ver</th>
                        <th className="th">Status</th>
                        <th className="th">By</th>
                        <th className="th">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(history.data ?? []).map((version) => (
                        <tr key={version.id}>
                          <td className="td tnum">{version.version_no}</td>
                          <td className="td">
                            <span
                              className={`chip ${
                                version.status === 'current'
                                  ? 'bg-emerald-50 text-emerald-700'
                                  : version.status === 'pending'
                                    ? 'bg-amber-50 text-amber-800'
                                    : 'bg-slate-100 text-slate-600'
                              }`}
                            >
                              {version.status}
                            </span>
                          </td>
                          <td className="td text-sm">
                            {version.created_by}
                            <div className="text-xs text-ink-mute">
                              {fmtDate(version.created_at)}
                            </div>
                          </td>
                          <td className="td text-sm">{version.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {history.isLoading && <div className="p-3 text-sm text-ink-mute">Loading…</div>}
                  {history.data?.length === 0 && (
                    <div className="p-3 text-sm text-ink-mute">
                      No versions — this document has not been issued yet.
                    </div>
                  )}
                </div>
                <div className="mt-4 flex items-center justify-between">
                  <span className="text-sm text-ink-mute">
                    Total {inr(invoice.grand_total, true)}
                  </span>
                  <button className="btn-ghost" onClick={close}>Close</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  )
}
