// Story sharing services (frontend/src/utils/share.ts): the catalog of the share menu and of the
// Sharing preferences, after NewsBlur's third-party services. Keys match `SHARE_SERVICES` in
// accounts/prefs.py.
import { copyLink } from '../actions'
import { useUi } from '../stores/ui'
import type { Story } from '../types'
import { displayTitle } from './format'

/** What a service needs from the story; `body` is the HTML currently shown (feed or extracted text). */
export interface ShareInput {
  title: string
  url: string
  tags: string[]
  body: string
}

export interface ShareService {
  key: string
  label: string
  icon?: string
  /** A share page opened in a new tab. */
  link?: (s: ShareInput) => string
  /** A service handled in the browser. */
  run?: (s: ShareInput) => void
}

const e = encodeURIComponent

export const SHARE_SERVICES: ShareService[] = [
  { key: 'copyurl', label: 'Copy URL', icon: 'link', run: (s) => copyLink(s.url) },
  { key: 'copytext', label: 'Copy text', icon: 'copy', run: copyText },
  { key: 'print', label: 'Print', icon: 'printer', run: printStory },
  { key: 'email', label: 'Email', icon: 'mail', link: (s) => `mailto:?subject=${e(s.title)}&body=${e(s.url)}` },
  { key: 'facebook', label: 'Facebook', link: (s) => `https://www.facebook.com/sharer/sharer.php?u=${e(s.url)}` },
  { key: 'twitter', label: 'X / Twitter', link: (s) => `https://x.com/intent/post?text=${e(s.title)}&url=${e(s.url)}` },
  { key: 'bluesky', label: 'Bluesky', link: (s) => `https://bsky.app/intent/compose?text=${e(`${s.title} ${s.url}`)}` },
  { key: 'mastodon', label: 'Mastodon', link: (s) => `https://mastodon.social/share?text=${e(`${s.title} ${s.url}`)}` },
  { key: 'reddit', label: 'Reddit', link: (s) => `https://www.reddit.com/submit?url=${e(s.url)}&title=${e(s.title)}` },
  { key: 'hackernews', label: 'Hacker News', link: (s) => `https://news.ycombinator.com/submitlink?u=${e(s.url)}&t=${e(s.title)}` },
  { key: 'linkedin', label: 'LinkedIn', link: (s) => `https://www.linkedin.com/sharing/share-offsite/?url=${e(s.url)}` },
  { key: 'tumblr', label: 'Tumblr', link: (s) => `https://www.tumblr.com/widgets/share/tool?canonicalUrl=${e(s.url)}&title=${e(s.title)}` },
  { key: 'blogger', label: 'Blogger', link: (s) => `https://www.blogger.com/blog-this.g?n=${e(s.title)}&u=${e(s.url)}` },
  { key: 'livejournal', label: 'LiveJournal', link: (s) => `https://www.livejournal.com/update.bml?subject=${e(s.title)}&event=${e(s.url)}` },
  { key: 'pinterest', label: 'Pinterest', link: (s) => `https://www.pinterest.com/pin/find/?url=${e(s.url)}` },
  { key: 'flipboard', label: 'Flipboard', link: (s) => `https://share.flipboard.com/bookmarklet/popout?v=2&title=${e(s.title)}&url=${e(s.url)}` },
  { key: 'buffer', label: 'Buffer', link: (s) => `https://buffer.com/add?url=${e(s.url)}&text=${e(s.title)}` },
  { key: 'whatsapp', label: 'WhatsApp', link: (s) => `https://api.whatsapp.com/send?text=${e(`${s.title} ${s.url}`)}` },
  { key: 'telegram', label: 'Telegram', link: (s) => `https://t.me/share/url?url=${e(s.url)}&text=${e(s.title)}` },
  { key: 'lineme', label: 'LINE', link: (s) => `https://social-plugins.line.me/lineit/share?url=${e(s.url)}` },
  { key: 'instapaper', label: 'Instapaper', link: (s) => `https://www.instapaper.com/edit?url=${e(s.url)}&title=${e(s.title)}` },
  { key: 'readwise', label: 'Readwise', link: (s) => `https://readwise.io/save?url=${e(s.url)}&title=${e(s.title)}` },
  {
    key: 'pinboard', label: 'Pinboard',
    link: (s) => `https://pinboard.in/add?url=${e(s.url)}&title=${e(s.title)}&tags=${e(s.tags.join(', '))}`,
  },
  {
    key: 'raindrop', label: 'Raindrop.io',
    link: (s) => `https://app.raindrop.io/add?link=${e(s.url)}&title=${e(s.title)}&tags=${e(s.tags.join(', '))}`,
  },
  {
    key: 'diigo', label: 'Diigo',
    link: (s) => `https://www.diigo.com/post?url=${e(s.url)}&title=${e(s.title)}&tags=${e(s.tags.join(', '))}`,
  },
]

export function shareInput(story: Story, body: string): ShareInput {
  return { title: displayTitle(story.title, story.preview, story.permalink), url: story.permalink, tags: story.tags, body }
}

/** The selected services, in catalog order. */
export function selectedServices(keys: string[]): ShareService[] {
  return SHARE_SERVICES.filter((service) => keys.includes(service.key))
}

export function share(service: ShareService, input: ShareInput) {
  if (service.run) return service.run(input)
  const link = service.link!(input)
  // A mailto: link hands over to the mail program without leaving an empty tab behind.
  if (link.startsWith('mailto:')) window.location.href = link
  else window.open(link, '_blank', 'noopener')
}

function plainText(html: string): string {
  return new DOMParser().parseFromString(html, 'text/html').body.textContent?.trim() ?? ''
}

async function copyText(s: ShareInput) {
  try {
    await navigator.clipboard.writeText(`${s.title}\n\n${s.url}\n\n${plainText(s.body)}`)
    useUi().toast('Text copied.', { seconds: 2 })
  } catch {
    useUi().error('The text could not be copied.')
  }
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

/** Print the story in a window of its own; the body was sanitized when it was stored. */
function printStory(s: ShareInput) {
  const win = window.open('', '_blank')
  if (!win) {
    useUi().error('The print window was blocked.')
    return
  }
  const title = escapeHtml(s.title)
  const url = escapeHtml(s.url)
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>
<style>body{font:16px/1.6 Georgia,serif;max-width:42em;margin:2em auto;padding:0 1em}img,video{max-width:100%;height:auto}
.link{font:12px sans-serif;word-break:break-all}</style></head>
<body><h1>${title}</h1><p class="link"><a href="${url}">${url}</a></p>${s.body}</body></html>`)
  win.document.close()
  // Print once the images have loaded.
  if (win.document.readyState === 'complete') win.print()
  else win.addEventListener('load', () => win.print())
}
