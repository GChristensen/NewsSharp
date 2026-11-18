// Media fallback through the image proxy (frontend/src/utils/media.ts): when an image,
// video, audio or source fails to load, it is retried once through /img with the story's signature.

export function proxyUrl(url: string | null, sigs: Record<string, string>): string | null {
  if (!url) return null
  const sig = sigs[url]
  return sig ? `/img?u=${encodeURIComponent(url)}&sig=${sig}` : null
}

/** Retries a failed media element through the proxy; false when there is nothing left to retry. */
export function mediaFallback(event: Event, sigs: Record<string, string>): boolean {
  const element = event.target as HTMLElement
  if (!element || !['IMG', 'VIDEO', 'AUDIO', 'SOURCE'].includes(element.tagName) || element.dataset.proxied) return false
  const proxied = proxyUrl(element.getAttribute('src'), sigs)
  if (!proxied) return false
  element.dataset.proxied = '1'
  element.removeAttribute('srcset')
  element.setAttribute('src', proxied)
  if (element.tagName === 'SOURCE') {
    const parent = element.parentElement
    if (parent && 'load' in parent) (parent as HTMLMediaElement).load()
  }
  return true
}

const VIDEO_ELEMENT = /<video\b([^>]*)>([\s\S]*?)<\/video>/g
const SOURCE_TAG = /<source\b([^>]*)>/g

function attribute(attrs: string, name: string): string {
  const match = attrs.match(new RegExp(`\\s${name}="([^"]*)"`))
  return match ? match[1].replace(/&amp;/g, '&') : ''
}

/** The video a grid card plays: the first MP4 among the `<video>` and `<source>` URLs of the
 *  sanitized content, otherwise the first of them. Sanitized URLs are already absolute. */
export function firstVideo(content: string | undefined): string | null {
  if (!content || !content.includes('<video')) return null
  const candidates: { src: string; type: string }[] = []
  for (const [, attrs, inner] of content.matchAll(VIDEO_ELEMENT)) {
    const src = attribute(attrs, 'src')
    if (src) candidates.push({ src, type: attribute(attrs, 'type') })
    for (const [, sourceAttrs] of inner.matchAll(SOURCE_TAG)) {
      const sourceSrc = attribute(sourceAttrs, 'src')
      if (sourceSrc) candidates.push({ src: sourceSrc, type: attribute(sourceAttrs, 'type') })
    }
  }
  const mp4 = candidates.find((c) => c.type === 'video/mp4' || /\.mp4(\?|#|$)/i.test(c.src))
  return (mp4 ?? candidates[0])?.src ?? null
}
