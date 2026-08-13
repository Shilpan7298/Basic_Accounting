/**
 * Tally export and the ledger mapping table.
 *
 * The preview deliberately runs first and shows any missing mapping with the
 * hint text from the backend, because the alternative — exporting to a ledger
 * name that does not exist in Tally — is worse than not exporting at all.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fmtDate, inr } from '../lib/api'
import type { LedgerMapping, TallyPreview } from '../lib/types'
import { ErrorBox, Field, Loading, Page, Stat } from '../components/ui'

function monthStart() {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
}

export default function Tally() {
  const queryClient = useQueryClient()
  const [from, setFrom] = useState(monthStart())
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10))
  const [preview, setPreview] = useState<TallyPreview | null>(null)
  const [edits, setEdits] = useState<Record<string, string>>({})

  const mappings = useQuery({
    queryKey: ['tally-mappings'],
    queryFn: () => api.get<LedgerMapping[]>('/tally/mappings'),
  })

  const runPreview = useMutation({
    mutationFn: () =>
      api.post<TallyPreview>('/tally/preview', { date_from: from, date_to: to, force: false }),
    onSuccess: setPreview,
  })

  const runExport = useMutation({
    mutationFn: (force: boolean) =>
      api.post<{ id: string; voucher_count: number }>('/tally/export', {
        date_from: from,
        date_to: to,
        delivery: 'FILE',
        force,
      }),
    onSuccess: (result) => {
      api.download(`/tally/exports/${result.id}/xml`)
      runPreview.mutate()
    },
  })

  const seed = useMutation({
    mutationFn: () => api.post('/tally/mappings/seed'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tally-mappings'] }),
  })

  const saveMapping = useMutation({
    mutationFn: (mapping: LedgerMapping) =>
      api.post('/tally/mappings', {
        scope: mapping.scope,
        local_key: mapping.local_key,
        tally_ledger_name: edits[mapping.id] ?? mapping.tally_ledger_name,
        tally_parent_group: mapping.tally_parent_group,
        notes: mapping.notes,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tally-mappings'] }),
  })

  return (
    <Page
      title="Tally export"
      subtitle="Tax invoices only — proformas carry no GST liability and are never exported"
    >
      <div className="card card-pad mb-5">
        <div className="grid gap-4 sm:grid-cols-4">
          <Field label="From">
            <input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label="To">
            <input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </Field>
          <div className="flex items-end gap-2 sm:col-span-2">
            <button className="btn-ghost" disabled={runPreview.isPending} onClick={() => runPreview.mutate()}>
              {runPreview.isPending ? 'Checking…' : 'Preview'}
            </button>
            <button
              className="btn-primary"
              disabled={runExport.isPending || !preview || preview.missing_mappings.length > 0}
              onClick={() => runExport.mutate(false)}
            >
              {runExport.isPending ? 'Exporting…' : 'Export XML'}
            </button>
            {preview && preview.voucher_count === 0 && preview.manifest.skipped_already_exported.length > 0 && (
              <button className="btn-ghost" onClick={() => runExport.mutate(true)}>
                Force re-export
              </button>
            )}
          </div>
        </div>
        {runPreview.error && <div className="mt-4"><ErrorBox error={runPreview.error} /></div>}
        {runExport.error && <div className="mt-4"><ErrorBox error={runExport.error} title="Export blocked" /></div>}
      </div>

      {preview && (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-4">
            <Stat label="Vouchers to export" value={preview.voucher_count} />
            <Stat label="Already exported" value={preview.manifest.skipped_already_exported.length} />
            <Stat label="Proformas excluded" value={preview.manifest.excluded_proformas} />
            <Stat label="Cancelled excluded" value={preview.manifest.excluded_cancelled} />
          </div>

          {preview.missing_mappings.length > 0 && (
            <div className="mb-5 rounded-lg border border-red-200 bg-red-50 p-4">
              <p className="text-sm font-semibold text-red-800">
                Export blocked — {preview.missing_mappings.length} ledger mapping
                {preview.missing_mappings.length > 1 ? 's are' : ' is'} missing
              </p>
              <p className="mt-1 text-sm text-red-700">
                Ledger names are never guessed. Fill these in below using the names exactly as they
                appear in Tally.
              </p>
              <ul className="mt-2 space-y-1 text-sm text-red-700">
                {preview.missing_mappings.map((missing) => (
                  <li key={`${missing.scope}-${missing.local_key}`}>
                    <code className="rounded bg-red-100 px-1">{missing.scope}</code> {missing.hint}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.manifest.invoices.length > 0 && (
            <div className="card mb-5 overflow-hidden">
              <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
                Manifest
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px]">
                  <thead>
                    <tr>
                      <th className="th">Invoice</th>
                      <th className="th">Date</th>
                      <th className="th">Customer</th>
                      <th className="th text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.manifest.invoices.map((invoice) => (
                      <tr key={invoice.id}>
                        <td className="td tnum font-medium">{invoice.number}</td>
                        <td className="td tnum">{fmtDate(invoice.date)}</td>
                        <td className="td">{invoice.customer}</td>
                        <td className="td tnum text-right">{inr(invoice.grand_total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-2">
          <span className="text-sm font-medium">Ledger mappings</span>
          <button className="btn-ghost" onClick={() => seed.mutate()} disabled={seed.isPending}>
            Seed placeholders
          </button>
        </div>
        {mappings.isLoading ? (
          <Loading what="mappings" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead>
                <tr>
                  <th className="th">Scope</th>
                  <th className="th">Key</th>
                  <th className="th">Tally ledger name</th>
                  <th className="th">Parent group</th>
                  <th className="th text-right">Save</th>
                </tr>
              </thead>
              <tbody>
                {(mappings.data ?? []).map((mapping) => (
                  <tr key={mapping.id}>
                    <td className="td">
                      <span className="chip bg-slate-100 text-slate-700">{mapping.scope}</span>
                    </td>
                    <td className="td font-mono text-xs text-ink-mute">{mapping.local_key}</td>
                    <td className="td">
                      <input
                        className="input"
                        value={edits[mapping.id] ?? mapping.tally_ledger_name}
                        onChange={(e) => setEdits({ ...edits, [mapping.id]: e.target.value })}
                      />
                    </td>
                    <td className="td text-ink-mute">{mapping.tally_parent_group ?? '—'}</td>
                    <td className="td text-right">
                      <button
                        className="btn-ghost"
                        disabled={
                          saveMapping.isPending ||
                          (edits[mapping.id] ?? mapping.tally_ledger_name) === mapping.tally_ledger_name
                        }
                        onClick={() => saveMapping.mutate(mapping)}
                      >
                        Save
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Page>
  )
}
