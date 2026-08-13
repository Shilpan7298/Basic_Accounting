import type { ReactNode } from 'react'

export function Page({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-ink-mute">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </div>
  )
}

const STATUS_TONE: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-700',
  received: 'bg-blue-50 text-blue-700',
  in_production: 'bg-amber-50 text-amber-700',
  dispatched: 'bg-violet-50 text-violet-700',
  invoiced: 'bg-indigo-50 text-indigo-700',
  partially_paid: 'bg-teal-50 text-teal-700',
  paid: 'bg-emerald-50 text-emerald-700',
  cancelled: 'bg-red-50 text-red-700',
  issued: 'bg-emerald-50 text-emerald-700',
  pending: 'bg-slate-100 text-slate-700',
  approved: 'bg-emerald-50 text-emerald-700',
  ordered: 'bg-indigo-50 text-indigo-700',
  needs_review: 'bg-amber-50 text-amber-800',
  failed: 'bg-red-50 text-red-700',
  succeeded: 'bg-emerald-50 text-emerald-700',
}

export function Status({ value }: { value: string }) {
  return (
    <span className={`chip ${STATUS_TONE[value] ?? 'bg-slate-100 text-slate-700'}`}>
      {value.replace(/_/g, ' ')}
    </span>
  )
}

/** Delivery countdown. Overdue and near-due have to be visible at a glance. */
export function Countdown({ days }: { days: number | null }) {
  if (days === null) return <span className="text-ink-mute">—</span>
  const tone =
    days < 0 ? 'bg-red-50 text-red-700' : days <= 7 ? 'bg-amber-50 text-amber-800' : 'bg-slate-100 text-slate-700'
  const label = days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? 'due today' : `${days}d left`
  return <span className={`chip tnum ${tone}`}>{label}</span>
}

export function Confidence({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return null
  const percent = Math.round(value * 100)
  const tone = value >= 0.8 ? 'bg-emerald-500' : value >= 0.5 ? 'bg-amber-500' : 'bg-red-500'
  return (
    <span className="inline-flex items-center gap-1.5" title={`confidence ${percent}%`}>
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-slate-200">
        <span className={`block h-full ${tone}`} style={{ width: `${Math.max(percent, 3)}%` }} />
      </span>
      <span className="tnum text-xs text-ink-mute">{percent}%</span>
    </span>
  )
}

export function Empty({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="card card-pad text-center">
      <p className="text-sm font-medium text-ink-soft">{message}</p>
      {hint && <p className="mt-1 text-sm text-ink-mute">{hint}</p>}
    </div>
  )
}

export function Loading({ what = 'data' }: { what?: string }) {
  return <div className="card card-pad text-sm text-ink-mute">Loading {what}…</div>
}

export function ErrorBox({ error, title = 'Something went wrong' }: { error: unknown; title?: string }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4">
      <p className="text-sm font-semibold text-red-800">{title}</p>
      <p className="mt-1 whitespace-pre-wrap text-sm text-red-700">{message}</p>
    </div>
  )
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: ReactNode
}) {
  return (
    <div>
      <span className="label">{label}</span>
      {children}
      {hint && <div className="mt-1 text-xs text-ink-mute">{hint}</div>}
    </div>
  )
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="card card-pad">
      <div className="text-xs uppercase tracking-wide text-ink-mute">{label}</div>
      <div className={`mt-1 text-lg font-semibold tnum ${tone ?? ''}`}>{value}</div>
    </div>
  )
}
