// The last opened reading scope (frontend/src/utils/lastScope.ts), for the start page's Continue row
// and the "last scope" start page preference. Kept per browser: it is a convenience, not state.

const KEY = 'newssharp.lastScope'

/** Only unread rivers are remembered: saved scopes and searches are not something to resume. */
export function rememberScope(scope: string): void {
  if (!/^(all|feed:\d+|folder:\d+)$/.test(scope)) return
  try {
    localStorage.setItem(KEY, scope)
  } catch {
    /* storage can be unavailable (private windows, blocked site data) */
  }
}

export function lastScope(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
