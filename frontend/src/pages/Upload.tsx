import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { Health, UploadResponse } from '../lib/types'
import { ErrorBox, Field, Page } from '../components/ui'

export default function Upload() {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [schema, setSchema] = useState('customer_po')
  const [extractor, setExtractor] = useState('')
  const health = useQuery({ queryKey: ['health'], queryFn: () => api.get<Health>('/health') })

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append('file', file!)
      form.append('schema_name', schema)
      if (extractor) form.append('extractor', extractor)
      return api.upload<UploadResponse>('/uploads', form)
    },
    onSuccess: (result) =>
      navigate(`/review/${result.extraction.id}?document=${result.document_id}`),
  })

  return (
    <Page
      title="Upload a document"
      subtitle="Extract it, review it side by side, then approve. Nothing is saved before you approve."
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card card-pad lg:col-span-2">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Document type">
              <select className="input" value={schema} onChange={(e) => setSchema(e.target.value)}>
                <option value="customer_po">Customer purchase order (sales)</option>
                <option value="supplier_offer">Supplier offer / quotation (purchase)</option>
              </select>
            </Field>
            <Field
              label="Extractor"
              hint="Leave on the configured default unless you are testing a specific adapter."
            >
              <select className="input" value={extractor} onChange={(e) => setExtractor(e.target.value)}>
                <option value="">Configured default ({health.data?.extractor_selected ?? '…'})</option>
                {health.data?.extractors.map((adapter) => (
                  <option key={adapter.name} value={adapter.name} disabled={!adapter.available}>
                    {adapter.name} {adapter.available ? '' : '(unavailable)'}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="mt-4">
            <label
              className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2
                         border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center
                         hover:border-brand-500 hover:bg-brand-50"
            >
              <input
                type="file"
                accept="application/pdf,image/*"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <span className="text-sm font-medium text-ink-soft">
                {file ? file.name : 'Choose a PDF, or drop one here'}
              </span>
              <span className="mt-1 text-xs text-ink-mute">
                {file
                  ? `${(file.size / 1024).toFixed(0)} KB`
                  : 'A PDF with a text layer extracts without needing a vision model.'}
              </span>
            </label>
          </div>

          {upload.error && (
            <div className="mt-4">
              <ErrorBox error={upload.error} title="Upload failed" />
            </div>
          )}

          <div className="mt-4 flex justify-end">
            <button
              className="btn-primary"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}
            >
              {upload.isPending ? 'Extracting…' : 'Upload and extract'}
            </button>
          </div>
        </div>

        <div className="card card-pad">
          <h2 className="text-sm font-semibold">What happens next</h2>
          <ol className="mt-2 space-y-2 text-sm text-ink-soft">
            <li>
              <strong>1.</strong> The text layer is read locally. No model call is needed to get the
              text itself.
            </li>
            <li>
              <strong>2.</strong> The configured extractor fills in the fields and reports a
              confidence for each.
            </li>
            <li>
              <strong>3.</strong> You see the original document beside the extracted fields and
              correct anything wrong.
            </li>
            <li>
              <strong>4.</strong> Approving creates the order or offer. Until then nothing exists.
            </li>
          </ol>
          {health.data && (
            <div className="mt-4 border-t border-slate-200 pt-3">
              <p className="text-xs uppercase tracking-wide text-ink-mute">Adapters</p>
              <ul className="mt-1 space-y-1 text-xs">
                {health.data.extractors.map((adapter) => (
                  <li key={adapter.name} className="flex items-start gap-2">
                    <span className={adapter.available ? 'text-emerald-600' : 'text-slate-400'}>
                      {adapter.available ? '●' : '○'}
                    </span>
                    <span>
                      <strong>{adapter.name}</strong>
                      <span className="text-ink-mute"> — {adapter.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </Page>
  )
}
