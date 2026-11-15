// Media fallback through the image proxy (frontend/src/utils/media.ts), R14.3: when an image,
// video, audio or source fails to load, it is retried once through /img with the story's signature.

export function proxyUrl(url: string | null, sigs: Record<string, string>): string | null {
  if (!url) return null
  const sig = sigs[url]
  return sig ? `/img?u=${encodeURIComponent(url)}&sig=${sig}` : null
}

export function mediaFallback(event: Event, sigs: Record<string, string>) {
  const element = event.target as HTMLElement
  if (!element || !['IMG', 'VIDEO', 'AUDIO', 'SOURCE'].includes(element.tagName) || element.dataset.proxied) return
  const proxied = proxyUrl(element.getAttribute('src'), sigs)
  if (!proxied) return
  element.dataset.proxied = '1'
  element.removeAttribute('srcset')
  element.setAttribute('src', proxied)
  if (element.tagName === 'SOURCE') {
    const parent = element.parentElement
    if (parent && 'load' in parent) (parent as HTMLMediaElement).load()
  }
}

/** The first video URL of sanitized story content, for cards of video-only stories (R9.10). */
export function firstVideo(content: string | undefined): string | null {
  if (!content) return null
  const match = content.match(/<video[^>]*\ssrc="([^"]+)"/) ?? content.match(/<video[^>]*>\s*<source[^>]*\ssrc="([^"]+)"/)
  return match ? match[1].replace(/&amp;/g, '&') : null
}
