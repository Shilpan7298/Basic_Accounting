/**
 * The only place that talks HTTP.
 *
 * Money arrives from the API as strings, deliberately — routing a Decimal
 * through a JavaScript number is how paise go missing. Nothing here parses
 * money into a `number`; it is formatted for display and sent back as a string.
 */

const BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message)
  }
}

function actor(): string {
  return localStorage.getItem('urjapod.actor') || 'office'
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Actor': actor() }
  if (init.body && !(init.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string>) },
  })

  if (!response.ok) {
    let detail: unknown
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = body?.detail ?? body
      if (typeof detail === 'string') message = detail
      else if (Array.isArray(detail)) message = detail.map((d) => d.msg ?? String(d)).join('; ')
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status, detail)
  }

  if (response.status === 204) return undefined as T
  const type = response.headers.get('content-type') || ''
  if (!type.includes('application/json')) return (await response.blob()) as T
  return response.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
  download: (path: string) => {
    window.open(`${BASE}${path}`, '_blank')
  },
  fileUrl: (path: string) => `${BASE}${path}`,
  setActor: (value: string) => localStorage.setItem('urjapod.actor', value),
  getActor: actor,
}

/** Indian digit grouping, from a string amount. Never uses Number arithmetic. */
export function inr(value: string | number | null | undefined, withSymbol = false): string {
  if (value === null || value === undefined || value === '') return '—'
  const raw = String(value)
  const negative = raw.trim().startsWith('-')
  const [wholeRaw, fracRaw = ''] = raw.replace('-', '').split('.')
  const frac = (fracRaw + '00').slice(0, 2)
  let whole = wholeRaw.replace(/^0+(?=\d)/, '')
  if (whole.length > 3) {
    const tail = whole.slice(-3)
    let head = whole.slice(0, -3)
    const groups: string[] = []
    while (head.length > 2) {
      groups.unshift(head.slice(-2))
      head = head.slice(0, -2)
    }
    if (head) groups.unshift(head)
    whole = `${groups.join(',')},${tail}`
  }
  return `${negative ? '-' : ''}${withSymbol ? '₹ ' : ''}${whole}.${frac}`
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  const [y, m, d] = value.slice(0, 10).split('-')
  return `${d}-${m}-${y}`
}

/** Percentages arrive as NUMERIC(18,4), so "30.0000" → "30%", "33.3300" → "33.33%". */
export function pct(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const trimmed = String(value).includes('.')
    ? String(value).replace(/0+$/, '').replace(/\.$/, '')
    : String(value)
  return `${trimmed}%`
}
