import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, inr } from '../lib/api'
import type { ReportPayload } from '../lib/types'
import { ErrorBox, Field, Loading, Page } from '../components/ui'

const REPORTS = [
  { key: 'sales-register', label: 'Sales register', ranged: true },
  { key: 'hsn-summary', label: 'HSN summary (GSTR-1 shape)', ranged: true },
  { key: 'purchase-register', label: 'Purchase register', ranged: true },
  { key: 'outstanding-receivables', label: 'Outstanding receivables', ranged: false },
  { key: 'order-pipeline', label: 'Order pipeline', ranged: false },
]

/** Columns whose values are money and should be right-aligned and grouped. */
const MONEY_COLUMNS = new Set([
  'Taxable', 'CGST', 'SGST', 'IGST', 'Cess', 'Round Off', 'Total', 'Tax',
  'Invoiced', 'Received', 'Balance', 'Order Value', 'Billed', 'Unbilled', 'Qty',
])

function monthStart() {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10)
}

export default function Reports() {
  const [report, setReport] = useState('sales-register')
  const [from, setFrom] = useState(monthStart())
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10))

  const definition = REPORTS.find((r) => r.key === report)!
  const query = definition.ranged
    ? `?date_from=${from}&date_to=${to}`
    : ''

  const data = useQuery({
    queryKey: ['report', report, from, to],
    queryFn: () => api.get<ReportPayload>(`/reports/${report}${query}`),
  })

  return (
    <Page
      title="Reports"
      subtitle="Proformas are excluded from every GST-shaped figure"
      actions={
        <>
          <button
            className="btn-ghost"
            onClick={() => api.download(`/reports/${report}/excel${query}`)}
          >
            Export this sheet
          </button>
          <button
            className="btn-primary"
            onClick={() => api.download(`/reports/all/excel?date_from=${from}&date_to=${to}`)}
          >
            Export all to Excel
          </button>
        </>
      }
    >
      <div className="card card-pad mb-5">
        <div className="grid gap-4 sm:grid-cols-4">
          <Field label="Report">
            <select className="input" value={report} onChange={(e) => setReport(e.target.value)}>
              {REPORTS.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          {definition.ranged && (
            <>
              <Field label="From">
                <input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="To">
                <input className="input" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </Field>
            </>
          )}
        </div>
      </div>

      {data.isLoading && <Loading what="the report" />}
      {data.error && <ErrorBox error={data.error} />}
      {data.data && (
        <div className="card overflow-hidden">
          <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium">
            {data.data.title}
          </div>
          {data.data.rows.length === 0 ? (
            <div className="p-4 text-sm text-ink-mute">Nothing to report for this period.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px]">
                <thead>
                  <tr>
                    {data.data.columns.map((column) => (
                      <th
                        key={column}
                        className={`th ${MONEY_COLUMNS.has(column) ? 'text-right' : ''}`}
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.data.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="hover:bg-slate-50">
                      {row.map((cell, cellIndex) => {
                        const column = data.data!.columns[cellIndex]
                        const isMoney = MONEY_COLUMNS.has(column)
                        return (
                          <td
                            key={cellIndex}
                            className={`td ${isMoney ? 'tnum text-right' : ''}`}
                          >
                            {isMoney ? inr(cell) : cell}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
                {Object.keys(data.data.totals).length > 0 && (
                  <tfoot>
                    <tr className="bg-slate-50 font-semibold">
                      <td className="td" colSpan={data.data.columns.length - 1}>
                        Total
                      </td>
                      <td className="td tnum text-right">
                        {inr(Object.values(data.data.totals).slice(-1)[0])}
                      </td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </div>
      )}
    </Page>
  )
}
