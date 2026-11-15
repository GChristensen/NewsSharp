// Thin fetch wrapper (frontend/src/api.ts): relative URLs (R5.2), the CSRF header and X-Client-Id
// (R21.4), a 15 s timeout and one retry for idempotent GET requests (R17.7).

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export const clientId = Math.random().toString(36).slice(2) + Date.now().toString(36)

let unauthorizedHandler: (() => void) | null = null
export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

interface Options {
  signal?: AbortSignal
  timeout?: number
  form?: FormData
  quiet401?: boolean
}

async function once<T>(method: string, url: string, body: unknown, options: Options): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new DOMException('timeout', 'TimeoutError')), options.timeout ?? 15000)
  const outer = options.signal
  const forward = () => controller.abort(outer!.reason)
  if (outer) {
    if (outer.aborted) forward()
    else outer.addEventListener('abort', forward, { once: true })
  }
  const headers: Record<string, string> = { 'X-Client-Id': clientId, Accept: 'application/json' }
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken()
  let payload: BodyInit | undefined
  if (options.form) payload = options.form
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  try {
    const response = await fetch(url, { method, headers, body: payload, signal: controller.signal, credentials: 'same-origin' })
    const isJson = (response.headers.get('content-type') || '').includes('json')
    const data = isJson ? await response.json() : null
    if (!response.ok) {
      if (response.status === 401 && !options.quiet401 && data?.error === 'unauthenticated') unauthorizedHandler?.()
      const error = new ApiError(response.status, data?.error || 'error', data?.message || `Request failed (${response.status})`)
      ;(error as any).data = data
      throw error
    }
    return data as T
  } finally {
    clearTimeout(timer)
    outer?.removeEventListener('abort', forward)
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export async function api<T = any>(method: string, url: string, body?: unknown, options: Options = {}): Promise<T> {
  try {
    return await once<T>(method, url, body, options)
  } catch (error) {
    const retriable =
      method === 'GET' && !options.signal?.aborted && (!(error instanceof ApiError) || error.status === 502 || error.status === 503)
    if (!retriable) throw normalize(error)
    try {
      return await once<T>(method, url, body, options)
    } catch (second) {
      throw normalize(second)
    }
  }
}

function normalize(error: unknown): unknown {
  if (error instanceof ApiError || isAbort(error)) return error
  if (error instanceof DOMException && error.name === 'TimeoutError') return new ApiError(0, 'timeout', 'The request timed out.')
  return new ApiError(0, 'network', 'The server could not be reached.')
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
