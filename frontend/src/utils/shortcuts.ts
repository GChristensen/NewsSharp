// Keyboard shortcuts (frontend/src/utils/shortcuts.ts), item 11. The handler and the shortcuts
// dialog use this one table (R11.3). No shortcut uses Alt or Meta (R11.2); the Ctrl shortcuts are
// global: they also work while an input has the focus, and they replace the browser's own action.
import {
  cycleSidebarMode, cycleView, effectiveLayout, markScopeRead, nextUnread, oldestUnread, openAll, openInNewTab, refreshFeed,
  selectStory, stepFeed, stepStory, toggleView,
} from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useUi } from '../stores/ui'
import type { Story } from '../types'

export interface ShortcutContext {
  focusFilter(): void
  focusSearch(): void
}

interface Shortcut {
  keys: string[]
  label: string
  run(context: ShortcutContext): void
}

let collapsed: Story | null = null

function scroller(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.story-pane') ?? document.querySelector<HTMLElement>('.list-pane')
}

function page(direction: 1 | -1) {
  const settings = useSettings()
  const el = scroller()
  if (!el) return
  const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 2
  if (direction === 1 && atEnd && settings.prefs.space_bar_action === 'next_unread') {
    nextUnread()
    return
  }
  el.scrollBy({ top: (direction * el.clientHeight * settings.prefs.space_scroll_spacing) / 100 })
}

function vertical(direction: 1 | -1) {
  const settings = useSettings()
  if (settings.prefs.keyboard_verticalarrows === 'scroll') scroller()?.scrollBy({ top: direction * settings.prefs.arrow_scroll_spacing })
  else stepStory(direction)
}

function horizontal(direction: 1 | -1) {
  if (useSettings().prefs.keyboard_horizontalarrows === 'site') stepFeed(direction)
  else cycleView(direction)
}

function withSelected(action: (story: Story) => void) {
  const story = useStories().selected
  if (story) action(story)
}

function toggleExpanded() {
  const stories = useStories()
  if (!['list', 'grid', 'magazine'].includes(effectiveLayout())) return
  if (stories.selected) {
    collapsed = stories.selected
    selectStory(null)
  } else if (collapsed && stories.find(collapsed.key)) {
    selectStory(collapsed)
  }
}

function toggleReadFilter() {
  const stories = useStories()
  const settings = useSettings()
  if (!stories.scope || stories.isSaved) return
  const next = settings.effective(stories.scope, 'read_filter') === 'unread' ? 'all' : 'unread'
  settings.setView(stories.scope, 'read_filter', next)
  stories.reload()
}

export const SHORTCUTS: Shortcut[] = [
  { keys: ['?'], label: 'Keyboard shortcuts', run: () => useUi().openDialog('shortcuts') },
  { keys: ['j', 'ArrowDown'], label: 'Next story', run: () => vertical(1) },
  { keys: ['k', 'ArrowUp'], label: 'Previous story', run: () => vertical(-1) },
  { keys: ['n'], label: 'Next unread story, across feeds', run: () => nextUnread() },
  { keys: ['p', 'b'], label: 'Previous story in reading history', run: () => useStories().previousInHistory() },
  { keys: ['Shift+j', 'Shift+n', 'Shift+ArrowDown'], label: 'Next feed', run: () => stepFeed(1) },
  { keys: ['Shift+k', 'Shift+p', 'Shift+ArrowUp'], label: 'Previous feed', run: () => stepFeed(-1) },
  { keys: ['ArrowLeft'], label: 'Previous view (or previous feed)', run: () => horizontal(-1) },
  { keys: ['ArrowRight'], label: 'Next view (or next feed)', run: () => horizontal(1) },
  { keys: ['h'], label: 'Previous view', run: () => cycleView(-1) },
  { keys: ['l'], label: 'Next view', run: () => cycleView(1) },
  { keys: ['Enter'], label: 'Toggle Text view', run: () => toggleView('text') },
  { keys: ['Shift+Enter'], label: 'Toggle Story view', run: () => toggleView('story') },
  { keys: ['Space'], label: 'Page down; at the end, next unread story', run: () => page(1) },
  { keys: ['Shift+Space'], label: 'Page up', run: () => page(-1) },
  { keys: ['r'], label: "Reload the open feed's stories", run: () => useStories().reload() },
  { keys: ['Shift+r'], label: 'Fetch the open feed now', run: () => { const id = useStories().feedId; if (id !== null) refreshFeed(id) } },
  { keys: ['u', 'm'], label: 'Toggle read / unread', run: () => withSelected((s) => useStories().toggleRead(s)) },
  { keys: ['Shift+m'], label: 'Go to the oldest unread story', run: () => oldestUnread() },
  { keys: ['s'], label: 'Save for later / remove', run: () => withSelected((s) => (s.saved ? useStories().unsave(s) : useStories().save(s))) },
  { keys: ['o', 'v'], label: 'Open the story in a background tab', run: () => withSelected((s) => openInNewTab(s.permalink, true)) },
  { keys: ['Shift+v'], label: 'Open the story in a new tab', run: () => withSelected((s) => openInNewTab(s.permalink)) },
  { keys: ['x'], label: 'Collapse / expand the story (List, Grid, Magazine)', run: toggleExpanded },
  { keys: ['Shift+y'], label: 'Mark newer stories as read', run: () => withSelected((s) => useStories().markRelative(s, 'newer')) },
  { keys: ['Shift+b'], label: 'Mark older stories as read', run: () => withSelected((s) => useStories().markRelative(s, 'older')) },
  { keys: ['Shift+a'], label: 'Mark all as read', run: () => markScopeRead(0, undefined, { undoable: true }) },
  { keys: ['Shift+e'], label: 'Open All Site Stories', run: () => openAll() },
  { keys: ['Shift+u'], label: 'Toggle the sidebar', run: () => useSettings().set('sidebar_collapsed', !useSettings().prefs.sidebar_collapsed) },
  { keys: ['Shift+f'], label: 'Full screen reading', run: () => (useUi().fullscreen = !useUi().fullscreen) },
  { keys: ['Shift+l', 'Ctrl+u'], label: 'Toggle the read filter (Unread / All)', run: toggleReadFilter },
  { keys: ['+'], label: 'Next sidebar mode (All, Unread, Saved for Later)', run: () => cycleSidebarMode(1) },
  { keys: ['-'], label: 'Previous sidebar mode', run: () => cycleSidebarMode(-1) },
  { keys: ['g'], label: 'Jump to a feed by name', run: (context) => context.focusFilter() },
  { keys: ['/'], label: 'Search', run: (context) => context.focusSearch() },
  { keys: ['a'], label: 'Add a site', run: () => useUi().openDialog('add') },
  { keys: ['Escape'], label: 'Close; otherwise deselect the story', run: () => selectStory(null) },
]

function combo(event: KeyboardEvent): string {
  if (event.ctrlKey) return `Ctrl+${event.shiftKey ? 'Shift+' : ''}${event.key.toLowerCase()}`
  let key = event.key
  if (key === ' ') key = 'Space'
  if (key.length === 1) {
    if (/[a-z]/i.test(key)) return (event.shiftKey ? 'Shift+' : '') + key.toLowerCase()
    return key // symbols such as ? + - / already include the Shift state
  }
  return (event.shiftKey ? 'Shift+' : '') + key
}

const LOOKUP = new Map<string, Shortcut>()
for (const shortcut of SHORTCUTS) for (const key of shortcut.keys) LOOKUP.set(key, shortcut)

/** Shortcuts are inactive in inputs and editable elements and while a dialog is open. */
export function handleKey(event: KeyboardEvent, context: ShortcutContext): void {
  if (event.altKey || event.metaKey || event.defaultPrevented) return
  const ui = useUi()
  if (event.key === 'Escape' && (ui.menu || ui.stylePopover)) {
    ui.closeMenu()
    ui.stylePopover = null
    return
  }
  if (ui.dialog) return
  const target = event.target as HTMLElement | null
  const editing = !!target && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)
  const shortcut = LOOKUP.get(combo(event))
  if (!shortcut || (editing && !event.ctrlKey)) return
  event.preventDefault()
  shortcut.run(context)
}

export function keyLabel(key: string): string {
  return key.replace('Arrow', '').replace('Down', '↓').replace('Up', '↑').replace('Left', '←').replace('Right', '→').replace('Escape', 'Esc')
}
