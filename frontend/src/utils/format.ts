// Dates and text helpers (frontend/src/utils/format.ts), R9.5, R9.7, R12.7. Dates use
// Intl.DateTimeFormat in the user's time zone; there is no date library.

function parts(date: Date, timeZone: string): { y: number; m: number; d: number } {
  const formatted = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
  const [y, m, d] = formatted.split('-').map(Number)
  return { y, m, d }
}

function safeZone(timeZone: string): string {
  try {
    new Intl.DateTimeFormat('en', { timeZone })
    return timeZone
  } catch {
    return 'UTC'
  }
}

/** Time only for today, "Yesterday, <time>", weekday and date for this month, full date otherwise. */
export function storyDate(seconds: number, timeZone: string, hour12: boolean, now = new Date()): string {
  const zone = safeZone(timeZone)
  const date = new Date(seconds * 1000)
  const time = new Intl.DateTimeFormat('en', { timeZone: zone, hour: 'numeric', minute: '2-digit', hour12 }).format(date)
  const a = parts(date, zone)
  const b = parts(now, zone)
  const dayNumber = (p: { y: number; m: number; d: number }) => Math.round(Date.UTC(p.y, p.m - 1, p.d) / 86400000)
  const diff = dayNumber(b) - dayNumber(a)
  if (diff === 0) return time
  if (diff === 1) return `Yesterday, ${time}`
  if (a.y === b.y && a.m === b.m) {
    const day = new Intl.DateTimeFormat('en', { timeZone: zone, weekday: 'long', month: 'long', day: 'numeric' }).format(date)
    return `${day}, ${time}`
  }
  const full = new Intl.DateTimeFormat('en', { timeZone: zone, weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(date)
  return `${full}, ${time}`
}

export function shortDate(seconds: number, timeZone: string, hour12: boolean, now = new Date()): string {
  const zone = safeZone(timeZone)
  const date = new Date(seconds * 1000)
  const a = parts(date, zone)
  const b = parts(now, zone)
  if (a.y === b.y && a.m === b.m && a.d === b.d) {
    return new Intl.DateTimeFormat('en', { timeZone: zone, hour: 'numeric', minute: '2-digit', hour12 }).format(date)
  }
  const options: Intl.DateTimeFormatOptions = { timeZone: zone, month: 'short', day: 'numeric' }
  if (a.y !== b.y) options.year = 'numeric'
  return new Intl.DateTimeFormat('en', options).format(date)
}

export function dateTime(seconds: number, timeZone: string, hour12: boolean): string {
  if (!seconds) return 'never'
  return new Intl.DateTimeFormat('en', {
    timeZone: safeZone(timeZone), year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12,
  }).format(new Date(seconds * 1000))
}

export function relativeTime(seconds: number, now = Date.now() / 1000): string {
  if (!seconds) return 'never'
  const diff = seconds - now
  const abs = Math.abs(diff)
  const unit = abs < 90 ? [1, 'second'] : abs < 5400 ? [60, 'minute'] : abs < 129600 ? [3600, 'hour'] : [86400, 'day']
  const n = Math.max(1, Math.round(abs / (unit[0] as number)))
  const text = `${n} ${unit[1]}${n === 1 ? '' : 's'}`
  return diff < 0 ? `${text} ago` : `in ${text}`
}

export function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** Escaped text with the search terms wrapped in <mark> (R12.7). */
export function highlight(text: string, terms: string[]): string {
  const escaped = escapeHtml(text)
  const words = terms.map((t) => t.trim()).filter((t) => t.length > 1)
  if (!words.length) return escaped
  const pattern = words.map((w) => escapeHtml(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  return escaped.replace(new RegExp(`(${pattern})`, 'gi'), '<mark>$1</mark>')
}

const PREVIEW_LINES: Record<string, number> = { title: 0, small: 300, medium: 600, large: 1000 }
const PREVIEW_CARDS: Record<string, number> = { title: 0, small: 500, medium: 1000, large: 1500 }

/** R9.7: the preview text of a title row (one line) or of a card (paragraph breaks kept). */
export function previewText(preview: string, title: string, setting: string, card: boolean): string {
  const limit = (card ? PREVIEW_CARDS : PREVIEW_LINES)[setting] ?? 0
  if (!limit || !preview) return ''
  if (card) return preview.slice(0, limit)
  const line = preview.replace(/\s+/g, ' ').trim()
  if (line.length < 30 || line.toLowerCase().startsWith(title.trim().toLowerCase().slice(0, 60)) && title.trim().length > 10) return ''
  return line.slice(0, limit)
}

/** R2.26: a story without a title shows the start of its text, or its link. */
export function displayTitle(title: string, preview: string, permalink: string): string {
  if (title) return title
  const text = preview.replace(/\s+/g, ' ').trim()
  return text ? text.slice(0, 80) : permalink || '(untitled)'
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1048576).toFixed(1)} MB`
}

/** R20.10: the feed URL appended to /subscribe/, with or without URL encoding. */
export function subscribeUrlFromLocation(pathAndQuery: string): string {
  let raw = pathAndQuery.replace(/^\/subscribe\/?/, '')
  if (!raw) return ''
  if (/^https?%3A/i.test(raw) || !/^https?:/i.test(raw)) {
    try {
      const decoded = decodeURIComponent(raw)
      if (/^http/i.test(decoded)) raw = decoded
    } catch {
      /* keep raw */
    }
  }
  raw = raw.replace(/^(https?):\/(?!\/)/i, '$1://')
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) raw = 'https://' + raw
  return raw
}
