/**
 * Exponential backoff retry utilities for fetch/API calls.
 *
 * Usage:
 *   import { fetchWithRetry } from '@/shared/utils/retry'
 *   const data = await fetchWithRetry<T>(url, options)
 *
 *   // Or wrap existing fetch:
 *   import { withRetry } from '@/shared/utils/retry'
 *   const result = await withRetry(() => someAsyncOp(), { maxRetries: 3 })
 */

/** Which HTTP status codes are retryable (transient). */
const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504])

export interface RetryOptions {
  /** Max retry attempts (default: 3). */
  maxRetries?: number
  /** Base delay in ms (default: 1000). */
  baseDelay?: number
  /** Max delay cap in ms (default: 30000). */
  maxDelay?: number
  /** Custom retryable check. Default: network errors + 5xx + 429. */
  isRetryable?: (error: unknown) => boolean
}

function calcDelay(attempt: number, baseDelay: number, maxDelay: number): number {
  const delay = Math.min(baseDelay * 2 ** attempt, maxDelay)
  const jitter = Math.random() * delay * 0.1
  return delay + jitter
}

/**
 * Generic retry wrapper for any async operation.
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  options?: RetryOptions,
): Promise<T> {
  const { maxRetries = 3, baseDelay = 1000, maxDelay = 30_000, isRetryable } = options ?? {}

  let lastError: unknown
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn()
    } catch (err) {
      lastError = err
      // If caller provided a custom retryable check and it returns false, bail immediately
      if (isRetryable && !isRetryable(err)) {
        throw err
      }
      if (attempt < maxRetries - 1) {
        const delay = calcDelay(attempt, baseDelay, maxDelay)
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
  throw lastError
}

/**
 * Fetch with exponential backoff retry.
 * Retries on network errors and transient HTTP status codes (429, 5xx).
 */
export async function fetchWithRetry<T>(
  url: string,
  options?: RequestInit,
  retryOpts?: RetryOptions,
): Promise<T> {
  const { maxRetries = 3, baseDelay = 1000, maxDelay = 30_000 } = retryOpts ?? {}

  let lastError: unknown
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const res = await fetch(url, options)

      if (res.ok) {
        if (res.status === 204) return undefined as T
        return (await res.json()) as T
      }

      // Non-retryable HTTP error — throw immediately
      if (!RETRYABLE_STATUSES.has(res.status)) {
        const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
        throw Object.assign(new Error(body.detail ?? `HTTP ${res.status}`), {
          status: res.status,
          body,
        })
      }

      // Retryable HTTP error — honor Retry-After header if present
      lastError = new Error(`HTTP ${res.status}`)
      if (attempt < maxRetries - 1) {
        const retryAfter = res.headers.get('Retry-After')
        const delay = retryAfter
          ? Math.min(Number(retryAfter) * 1000, maxDelay)
          : calcDelay(attempt, baseDelay, maxDelay)
        await new Promise((r) => setTimeout(r, delay))
      }
    } catch (err) {
      // Network error (fetch itself failed)
      if (err instanceof TypeError || (err as { name?: string })?.name === 'AbortError') {
        lastError = err
        if (attempt < maxRetries - 1) {
          const delay = calcDelay(attempt, baseDelay, maxDelay)
          await new Promise((r) => setTimeout(r, delay))
        }
      } else {
        // Non-retryable error (e.g., our thrown ApiError from non-retryable status)
        throw err
      }
    }
  }
  throw lastError
}
