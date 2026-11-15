// User actions that span stores and the router (frontend/src/actions.ts): opening scopes,
// navigation between stories and feeds (R10.5), marking read (R10.7), views and layouts (item 9).
import { nextTick } from 'vue'
import { api, errorMessage } from './api'
import { router, scopePath } from './router'
import { useSettings } from './stores/settings'
import { useStories } from './stores/stories'
import { useTree } from './stores/tree'
import { useUi } from './stores/ui'
import type { Feed, Folder, Layout, Story, View } from './types'

export const VIEWS: View[] = ['original', 'feed', 'text', 'story']
export const LAYOUTS: Layout[] = ['full', 'split', 'list', 'magazine', 'grid']

export function openScope(scope: string, storyId?: number | null) {
  const query = useStories().query.trim()
  return router.push({ path: scopePath(scope, storyId), query: query ? { q: query } : {} })
}

function savedMode(): boolean {
  return useSettings().prefs.sidebar_mode === 'saved'
}

export function feedScope(feedId: number): string {
  return savedMode() ? `saved:feed:${feedId}` : `feed:${feedId}`
}

export function openFeed(feed: Feed | { id: number }) {
  return openScope(feedScope(feed.id))
}

export function openFolder(folder: Folder) {
  return openScope(savedMode() ? `saved:folder:${folder.id}` : `folder:${folder.id}`)
}

export function openAll() {
  return openScope(savedMode() ? 'saved' : 'all')
}

export function scopeTitle(scope: string | null): string {
  if (!scope) return 'NewsSharp'
  const tree = useTree()
  const parts = scope.split(':')
  if (scope === 'all') return 'All Site Stories'
  if (scope === 'saved') return 'Saved for Later'
  if (parts[0] === 'saved' && parts[1] === 'tag') return `Saved for Later: ${parts.slice(2).join(':')}`
  const id = Number(parts[parts.length - 1])
  const kind = parts[parts.length - 2]
  const prefix = parts[0] === 'saved' ? 'Saved: ' : ''
  if (kind === 'feed') return prefix + (tree.feedById.get(id)?.title ?? tree.saved.titles[id] ?? 'Feed')
  return prefix + (tree.folderById.get(id)?.title ?? 'Folder')
}

/** The layout in effect: below 900 px Split behaves as List (R20.9). */
export function effectiveLayout(): Layout {
  const layout = useSettings().layout(useStories().scope)
  return layout === 'split' && useUi().narrow ? 'list' : layout
}

export function viewAvailable(view: View, layout = effectiveLayout()): boolean {
  if (view === 'original') {
    const stories = useStories()
    return (layout === 'split' || layout === 'full') && stories.feedId !== null && !stories.isSaved
  }
  return true
}

/** R9.1: views that are not available fall back to Feed without changing the stored setting. */
export function effectiveView(): View {
  const view = useSettings().view(useStories().scope)
  return viewAvailable(view) ? view : 'feed'
}

export function setView(view: View) {
  const scope = useStories().scope
  if (scope && viewAvailable(view)) useSettings().setView(scope, 'view', view)
}

export function toggleView(view: View) {
  setView(effectiveView() === view ? 'feed' : view)
}

export function cycleView(step: number) {
  const available = VIEWS.filter((v) => viewAvailable(v))
  const index = available.indexOf(effectiveView())
  setView(available[(index + step + available.length) % available.length])
}

/** Select a story and reflect it in the URL without a new history entry (R20.8). */
export function selectStory(story: Story | null) {
  const stories = useStories()
  stories.select(story)
  if (stories.scope && !stories.isSaved) {
    const query = router.currentRoute.value.query
    router.replace({ path: scopePath(stories.scope, story?.id ?? null), query })
  }
}

export async function stepStory(step: 1 | -1) {
  const stories = useStories()
  if (!stories.scope) return
  let index = stories.selectedIndex + step
  if (stories.selectedIndex === -1) index = step === 1 ? 0 : -1
  if (index >= stories.stories.length && !stories.ended) {
    await stories.loadMore() // R17.4: navigation passes the last loaded story
  }
  const story = stories.stories[index]
  if (story) selectStory(story)
  else if (step === 1 && stories.ended && stories.feedId !== null && !stories.isSaved) await nextUnreadFeed()
}

async function firstUnreadFrom(start: number): Promise<Story | null> {
  const stories = useStories()
  let index = start
  for (;;) {
    for (; index < stories.stories.length; index++) {
      if (!stories.stories[index].read) return stories.stories[index]
    }
    if (stories.ended || stories.error) return null
    const before = stories.stories.length
    await stories.loadMore()
    if (stories.stories.length === before && stories.ended) return null
    if (stories.stories.length === before && !stories.loading) return null
  }
}

function feedsWithUnreadAfter(feedId: number | null, step: 1 | -1, unreadOnly: boolean): Feed[] {
  const tree = useTree()
  const order = step === 1 ? tree.feedOrder : [...tree.feedOrder].reverse()
  const start = feedId === null ? -1 : order.findIndex((f) => f.id === feedId)
  const rotated = [...order.slice(start + 1), ...order.slice(0, Math.max(0, start))] // wraps around
  return unreadOnly ? rotated.filter((f) => f.unread > 0) : rotated
}

/** `n`: the next unread story; continues into the next feed with unread stories (R10.5). */
export async function nextUnread() {
  const stories = useStories()
  if (stories.scope && !stories.isSaved) {
    const story = await firstUnreadFrom(stories.selectedIndex + 1)
    if (story) return selectStory(story)
  }
  await nextUnreadFeed()
}

/** Past the end of a feed, navigation continues with the next feed that has unread stories. */
async function nextUnreadFeed() {
  const stories = useStories()
  const next = feedsWithUnreadAfter(stories.feedId, 1, true)[0]
  if (!next) {
    useUi().toast('No unread stories remain.')
    return
  }
  await openScope(`feed:${next.id}`)
  await nextTick()
  await stories.whenLoaded()
  const story = await firstUnreadFrom(0)
  if (story) selectStory(story)
}

/** `Shift+m`: go to the oldest unread story of the open scope. */
export async function oldestUnread() {
  const stories = useStories()
  if (!stories.scope || stories.isSaved) return
  while (!stories.ended && !stories.error && stories.stories.length < 300) await stories.loadMore()
  const unread = stories.stories.filter((s) => !s.read)
  if (!unread.length) return useUi().toast('No unread stories here.')
  selectStory(unread.reduce((a, b) => (b.story_date < a.story_date ? b : a)))
}

/** Next / previous feed in tree order; in Unread mode feeds without unread stories are skipped. */
export function stepFeed(step: 1 | -1) {
  const stories = useStories()
  const unreadOnly = useSettings().prefs.sidebar_mode === 'unread'
  const candidates = feedsWithUnreadAfter(stories.feedId, step, unreadOnly)
  if (candidates.length) openFeed(candidates[0])
}

/**
 * R10.7: mark the open scope read, with the confirmation for All Site Stories, then move on. With
 * `undoable` (the story titles header and `Shift+a`), a whole feed or folder gets an Undo toast.
 */
export function markScopeRead(days = 0, scope = useStories().scope, options: { undoable?: boolean } = {}) {
  if (!scope || scope.startsWith('saved')) return
  const settings = useSettings()
  const run = async () => {
    const result = await useStories().markScope(scope, days)
    if (!result) return
    if (options.undoable && result.undo_token) offerMarkReadUndo(scope, result.undo_token)
    if (days || scope !== useStories().scope) return
    if (settings.prefs.markread_nextfeed === 'nextfeed' && scope !== 'all') {
      const next = feedsWithUnreadAfter(useStories().feedId, 1, true)[0]
      if (next) openScope(`feed:${next.id}`)
    }
  }
  if (scope === 'all' && settings.prefs.mark_read_river_confirm) {
    useUi().openDialog('confirm', {
      title: 'Mark everything as read',
      message: days ? `Mark all stories older than ${days} day${days === 1 ? '' : 's'} as read?` : 'Mark all stories of all sites as read?',
      confirmLabel: 'Mark as read',
      onConfirm: run,
    })
  } else run()
}

/** The Undo toast of a marked feed or folder. Undo also returns to it if it is no longer open. */
function offerMarkReadUndo(scope: string, undoToken: string) {
  useUi().toast(`Marked ${scopeTitle(scope)} as read`, {
    seconds: 10,
    actionLabel: 'Undo',
    action: async () => {
      const stories = useStories()
      if ((await stories.undoMarkScope(undoToken)) && stories.scope !== scope) await openScope(scope)
    },
  })
}

/**
 * Mark one feed read from the sidebar (the unread count click, fork delta 016482272), then, as with
 * mark-all-read, open the next feed after it that has unread stories.
 */
export async function markFeedRead(feedId: number) {
  const result = await useStories().markScope(`feed:${feedId}`)
  if (!result ||useSettings().prefs.markread_nextfeed !== 'nextfeed') return
  const next = feedsWithUnreadAfter(feedId, 1, true)[0]
  if (next) await openScope(`feed:${next.id}`)
}

/** `Shift+r` and the context menu: a forced fetch; the list reloads on feed:fetched (R17.10). */
export async function refreshFeed(feedId: number) {
  const stories = useStories()
  try {
    await api('POST', `/api/feeds/${feedId}/refresh`)
    if (stories.scope === `feed:${feedId}`) stories.pendingRefresh = feedId
    useUi().toast('Refreshing the feed…', { seconds: 2 })
  } catch (error) {
    useUi().error(errorMessage(error))
  }
}

export function openInNewTab(url: string, background = false) {
  if (!url) return
  const tab = window.open(url, '_blank', 'noopener')
  if (background && tab) window.focus()
}

export async function copyLink(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    useUi().toast('Link copied.', { seconds: 2 })
  } catch {
    useUi().error('The link could not be copied.')
  }
}

export function cycleSidebarMode(step: number) {
  const modes = ['all', 'unread', 'saved'] as const
  const settings = useSettings()
  const index = modes.indexOf(settings.prefs.sidebar_mode)
  settings.set('sidebar_mode', modes[(index + step + modes.length) % modes.length])
}

/** R19.7: the titles pane size is a width for west and a height for north / south. */
export function setAnchor(anchor: 'west' | 'north' | 'south') {
  const settings = useSettings()
  const before = settings.prefs.story_pane_anchor
  if (before === anchor) return
  const size = settings.prefs.story_titles_pane_size
  if (before === 'west') settings.set('story_titles_pane_size', Math.max(68, Math.round(size / 2)))
  else if (anchor === 'west') settings.set('story_titles_pane_size', size * 2)
  settings.set('story_pane_anchor', anchor)
}
