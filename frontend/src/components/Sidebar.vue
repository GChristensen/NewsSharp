<!-- The sidebar (frontend/src/components/Sidebar.vue): header, special rows, the feed tree with
     drag and drop, fetch progress and the left taskbar. -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, provide, reactive, ref } from 'vue'
import {
  markFeedRead, markScopeRead, openAll, openFeed, openFolder, openHome, openInNewTab, openScope, refreshFeed, setFeedMuted,
} from '../actions'
import { api, errorMessage } from '../api'
import { useSession } from '../stores/session'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree, type TreeItem } from '../stores/tree'
import { useUi, type MenuItem } from '../stores/ui'
import type { Feed, Folder } from '../types'
import Icon from './Icon.vue'
import TreeNode, { type TreeContext } from './TreeNode.vue'

const tree = useTree()
const stories = useStories()
const settings = useSettings()
const session = useSession()
const ui = useUi()
const scroller = ref<HTMLElement | null>(null)
const filterInput = ref<HTMLInputElement | null>(null)

const mode = computed(() => settings.prefs.sidebar_mode)
const dragAllowed = computed(() => mode.value === 'all' && ui.feedFilter.trim() === '')
const allCount = computed(() => (mode.value === 'saved' ? tree.saved.total : tree.totalUnread))
const savedTags = computed(() => Object.entries(tree.saved.tags).sort((a, b) => a[0].localeCompare(b[0])))
const fetchedCount = computed(() => Math.max(0, tree.progressTotal - tree.unfetched))

// The feed filter is hidden until the funnel button shows it; the choice is a preference.
const filterShown = computed(() => settings.prefs.show_feed_filter)
// All Site Stories is optional (a preference); in Saved mode it is always shown, as All Saved Stories.
const showAllRow = computed(() => settings.prefs.show_all_site_stories || mode.value === 'saved')

function toggleFilter() {
  const show = !filterShown.value
  settings.set('show_feed_filter', show)
  if (show) nextTick(() => filterInput.value?.focus())
  else ui.feedFilter = ''
}

/** Feedxcavator news: sends a GET request to the configured feedxcavator handler. The request is made by the
 *  browser, so it carries the user's feedxcavator cookies; the response of the other origin is opaque and not read. */
async function runNewspaperHandler() {
  ui.toast('Requesting Feedxcavator news')
  try {
    await fetch(settings.prefs.feedxcavator_newspaper_url, { mode: 'no-cors', credentials: 'include' })
  } catch {
    ui.error('Feedxcavator news could not be requested.')
  }
}

defineExpose({
  focusFilter: () => {
    if (!filterShown.value) settings.set('show_feed_filter', true)
    nextTick(() => filterInput.value?.focus())
  },
})

// ------------------------------------------------------------------ rename

const renaming = reactive({ key: null as string | null, value: '' })

function startRename(key: string, value: string) {
  renaming.key = key
  renaming.value = value
}
async function commitRename() {
  const key = renaming.key
  if (!key) return
  renaming.key = null
  const [kind, id] = key.split(':')
  const value = renaming.value.trim()
  try {
    if (kind === 'feed') {
      const feed = tree.feedById.get(Number(id))
      if (feed && value !== feed.title) await tree.renameFeed(feed, value === feed.feed_title ? '' : value)
    } else {
      const folder = tree.folderById.get(Number(id))
      if (folder && value && value !== folder.title) await tree.renameFolder(folder, value)
    }
  } catch (error) {
    ui.error(errorMessage(error))
  }
}

// ------------------------------------------------------------------ drag and drop

interface DropTarget {
  parentId: number | null
  position: number
  intoFolderId: number | null
  lineTop: number | null
  lineLeft: number
}

const drag = reactive({ itemKey: null as string | null, intoFolderId: null as number | null })
const dropLine = reactive({ visible: false, top: 0, left: 0 })
const ghost = reactive({ visible: false, x: 0, y: 0, label: '' })
let dragItem: TreeItem | null = null
let startX = 0
let startY = 0
let active = false
let target: DropTarget | null = null
let suppressClick = false
let hoverFolder: number | null = null
let hoverTimer: number | undefined

function sameItem(a: TreeItem, b: TreeItem): boolean {
  return a.kind === b.kind && (a.kind === 'feed' ? a.feed.id === (b as any).feed.id : a.folder.id === (b as any).folder.id)
}

function pointerDown(event: PointerEvent, item: TreeItem) {
  if (event.button !== 0 || !dragAllowed.value || renaming.key) return
  dragItem = item
  startX = event.clientX
  startY = event.clientY
  active = false
  window.addEventListener('pointermove', pointerMove)
  window.addEventListener('pointerup', pointerUp)
  window.addEventListener('pointercancel', cancelDrag)
}

function siblingsWithout(parentId: number | null): TreeItem[] {
  return tree.children(parentId).filter((other) => !sameItem(other, dragItem!))
}

function validParent(parentId: number | null): boolean {
  // A folder cannot be dropped into itself or any of its descendants.
  if (dragItem!.kind !== 'folder' || parentId === null) return true
  return !tree.isDescendant(parentId, dragItem!.folder.id)
}

function computeTarget(x: number, y: number): DropTarget | null {
  const container = scroller.value!
  const box = container.getBoundingClientRect()
  if (x < box.left || x > box.right || y < box.top || y > box.bottom) return null
  const row = (document.elementFromPoint(x, y) as HTMLElement | null)?.closest<HTMLElement>('[data-tree-kind]')
  const lineY = (clientY: number) => clientY - box.top + container.scrollTop
  if (!row) {
    const rows = container.querySelectorAll<HTMLElement>('[data-tree-kind]')
    const last = rows[rows.length - 1]
    if (last && y < last.getBoundingClientRect().bottom) return null
    return { parentId: null, position: siblingsWithout(null).length, intoFolderId: null, lineTop: last ? lineY(last.getBoundingClientRect().bottom) : 0, lineLeft: 8 }
  }
  const rect = row.getBoundingClientRect()
  const ratio = (y - rect.top) / rect.height
  const id = Number(row.dataset.treeId)
  const indent = parseFloat(row.style.paddingLeft || '8')
  if (row.dataset.treeKind === 'feed') {
    const feed = tree.feedById.get(id)
    if (!feed || (dragItem!.kind === 'feed' && dragItem!.feed.id === id)) return null
    if (!validParent(feed.folder_id)) return null
    const siblings = siblingsWithout(feed.folder_id)
    const index = siblings.findIndex((s) => s.kind === 'feed' && s.feed.id === id)
    const after = ratio >= 0.5
    return { parentId: feed.folder_id, position: index + (after ? 1 : 0), intoFolderId: null, lineTop: lineY(after ? rect.bottom : rect.top), lineLeft: indent }
  }
  const folder = tree.folderById.get(id)
  if (!folder || (dragItem!.kind === 'folder' && dragItem!.folder.id === id)) return null
  hoverCollapsed(folder)
  const expandedWithChildren = !folder.collapsed && tree.children(folder.id).length > 0
  if (ratio < 0.25 || (ratio > 0.75 && !expandedWithChildren)) {
    if (!validParent(folder.parent_id)) return null
    const siblings = siblingsWithout(folder.parent_id)
    const index = siblings.findIndex((s) => s.kind === 'folder' && s.folder.id === id)
    const after = ratio > 0.75
    return { parentId: folder.parent_id, position: index + (after ? 1 : 0), intoFolderId: null, lineTop: lineY(after ? rect.bottom : rect.top), lineLeft: indent }
  }
  if (!validParent(folder.id)) return null
  if (ratio > 0.75) return { parentId: folder.id, position: 0, intoFolderId: null, lineTop: lineY(rect.bottom), lineLeft: indent + 22 }
  return { parentId: folder.id, position: siblingsWithout(folder.id).length, intoFolderId: folder.id, lineTop: null, lineLeft: 0 }
}

function hoverCollapsed(folder: Folder) {
  // Hovering a collapsed folder for 700 ms expands it.
  if (!folder.collapsed) return
  if (hoverFolder === folder.id) return
  clearTimeout(hoverTimer)
  hoverFolder = folder.id
  hoverTimer = window.setTimeout(() => {
    if (active && hoverFolder === folder.id) tree.setCollapsed(folder, false)
  }, 700)
}

function pointerMove(event: PointerEvent) {
  if (!dragItem) return
  if (!active) {
    if (Math.hypot(event.clientX - startX, event.clientY - startY) < 4) return // a drag starts after 4 px
    active = true
    drag.itemKey = dragItem.kind === 'feed' ? `feed:${dragItem.feed.id}` : `folder:${dragItem.folder.id}`
    ghost.label = dragItem.kind === 'feed' ? dragItem.feed.title : dragItem.folder.title
    ghost.visible = true
    document.body.style.userSelect = 'none'
  }
  ghost.x = event.clientX + 12
  ghost.y = event.clientY + 8
  const previousHover = hoverFolder
  target = computeTarget(event.clientX, event.clientY)
  const overRow = (document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null)?.closest<HTMLElement>('[data-tree-kind="folder"]')
  if (!overRow || Number(overRow.dataset.treeId) !== previousHover) {
    if (!overRow) {
      clearTimeout(hoverTimer)
      hoverFolder = null
    }
  }
  drag.intoFolderId = target?.intoFolderId ?? null
  dropLine.visible = !!target && target.lineTop !== null
  if (target && target.lineTop !== null) {
    dropLine.top = target.lineTop - 1
    dropLine.left = target.lineLeft
  }
  const box = scroller.value!.getBoundingClientRect()
  if (event.clientY < box.top + 24) scroller.value!.scrollTop -= 12
  else if (event.clientY > box.bottom - 24) scroller.value!.scrollTop += 12
}

function endDrag() {
  window.removeEventListener('pointermove', pointerMove)
  window.removeEventListener('pointerup', pointerUp)
  window.removeEventListener('pointercancel', cancelDrag)
  clearTimeout(hoverTimer)
  hoverFolder = null
  drag.itemKey = null
  drag.intoFolderId = null
  dropLine.visible = false
  ghost.visible = false
  document.body.style.userSelect = ''
  dragItem = null
  target = null
}

function cancelDrag() {
  active = false
  endDrag()
}

function pointerUp() {
  const item = dragItem
  const drop = target
  const wasActive = active
  active = false
  endDrag()
  if (!wasActive || !item) return
  suppressClick = true // a drag never opens the feed or folder
  setTimeout(() => (suppressClick = false), 0)
  if (drop) tree.move(item, drop.parentId, drop.position)
}

onBeforeUnmount(endDrag)

// ------------------------------------------------------------------ rows and menus

function clickFeed(feed: Feed) {
  if (!suppressClick) openFeed(feed)
}
function clickFolder(folder: Folder) {
  if (!suppressClick) openFolder(folder)
}

function feedMenu(event: MouseEvent, feed: Feed) {
  const items: MenuItem[] = [
    { label: 'Mark as read', icon: 'check-circle', tone: 'read', action: () => markFeedRead(feed.id), disabled: feed.unread === 0 },
    { label: 'Refresh now', icon: 'sync', tone: 'sync', action: () => refreshFeed(feed.id) },
    { label: 'Statistics', icon: 'bar-chart', tone: 'insight', action: () => ui.openDialog('statistics', { feedId: feed.id }) },
    { label: 'Settings…', icon: 'prefs-sliders', tone: 'organize', action: () => ui.openDialog('feed-settings', { feedId: feed.id }) },
    { label: 'Rename', icon: 'pencil', tone: 'organize', action: () => startRename(`feed:${feed.id}`, feed.title) },
    feed.muted
      ? { label: 'Unmute', icon: 'sound-on', tone: 'sound', action: () => setFeedMuted(feed, false) }
      : { label: 'Mute', icon: 'sound-off', tone: 'sound', action: () => setFeedMuted(feed, true) },
    { label: 'Open site', icon: 'globe', tone: 'web', action: () => openInNewTab(feed.feed_link || feed.feed_address), disabled: !feed.feed_link },
  ]
  items.push({ label: '', separator: true }, { label: 'Delete', icon: 'trash-can', danger: true, action: () => tree.remove({ kind: 'feed', feed }) })
  ui.openMenu(event, items)
}

function folderMenu(event: MouseEvent, folder: Folder) {
  ui.openMenu(event, [
    { label: 'Mark as read', icon: 'check-circle', tone: 'read', action: () => markScopeRead(0, `folder:${folder.id}`) },
    { label: 'Add site…', icon: 'rss-tile', tone: 'organize', action: () => ui.openDialog('add', { folderId: folder.id }) },
    { label: 'Rename', icon: 'pencil', tone: 'organize', action: () => startRename(`folder:${folder.id}`, folder.title) },
    { label: 'New subfolder', icon: 'folder-add', tone: 'organize', action: () => newSubfolder(folder) },
    { label: '', separator: true },
    { label: 'Delete', icon: 'trash-can', danger: true, action: () => tree.remove({ kind: 'folder', folder }) },
  ])
}

async function newSubfolder(parent: Folder) {
  try {
    const created = await api<Folder>('POST', '/api/folders', { title: 'New folder', parent_id: parent.id })
    if (parent.collapsed) tree.setCollapsed(parent, false)
    await tree.reload()
    await nextTick()
    startRename(`folder:${created.id}`, created.title)
  } catch (error) {
    ui.error(errorMessage(error))
  }
}

const context: TreeContext = {
  drag,
  renaming,
  pointerDown,
  clickFeed,
  clickFolder,
  feedMenu,
  folderMenu,
  commitRename,
  cancelRename: () => (renaming.key = null),
  markRead: (feed) => markFeedRead(feed.id),
  openSite: (feed) => openInNewTab(feed.feed_link || feed.feed_address),
}
provide('tree', context)

function manageMenu(event: MouseEvent) {
  const theme = (value: 'auto' | 'light' | 'dark', label: string, icon: string): MenuItem => ({
    label: `Theme: ${label}`, icon, tone: 'appearance', checked: settings.prefs.theme === value, action: () => settings.set('theme', value),
  })
  const items: MenuItem[] = [
    { label: 'Preferences', icon: 'prefs-sliders', tone: 'account', action: () => ui.openDialog('preferences') },
    { label: 'Import / Export (OPML)', icon: 'tray-swap', tone: 'organize', action: () => ui.openDialog('opml') },
    { label: 'Keyboard shortcuts', icon: 'keys', tone: 'insight', action: () => ui.openDialog('shortcuts') },
    { label: '', separator: true },
    theme('auto', 'Auto', 'theme-auto'),
    theme('light', 'Light', 'sun'),
    theme('dark', 'Dark', 'moon'),
    { label: '', separator: true },
    { label: 'Mark everything as read', icon: 'check-double', tone: 'read', action: () => markScopeRead(0, 'all') },
    { label: 'Change password', icon: 'key', tone: 'account', action: () => ui.openDialog('password') },
  ]
  if (session.user?.is_admin) {
    items.push({ label: 'Administration', icon: 'shield-check', tone: 'account', action: () => openInNewTab('/admin/') })
  }
  items.push({ label: '', separator: true }, { label: 'Log out', icon: 'door-out', tone: 'account', action: () => session.logout() })
  ui.openMenuAt(event.currentTarget as HTMLElement, items)
}
</script>

<template>
  <div class="bar">
    <button class="btn" title="Manage" @click="manageMenu"><Icon name="user" /> {{ session.user?.username }}</button>
    <span class="spacer" />
    <button class="btn icon-only" :class="{ active: !stories.scope }" title="Start page (Shift+H)" @click="openHome()"><Icon name="home" /></button>
    <button
      v-if="settings.prefs.feedxcavator_newspaper_url" class="btn icon-only" title="Feedxcavator news"
      @click="runNewspaperHandler"
    ><Icon name="newspaper" /></button>
    <button
      v-if="settings.prefs.feedxcavator_url" class="btn icon-only" title="Open feedxcavator"
      @click="openInNewTab(settings.prefs.feedxcavator_url)"
    ><Icon name="excavator" /></button>
    <button class="btn icon-only" :class="{ active: filterShown }" title="Filter feeds (G)" @click="toggleFilter"><Icon name="filter" /></button>
    <button class="btn icon-only" title="Collapse the sidebar (Shift+U)" @click="settings.set('sidebar_collapsed', true)">
      <Icon name="sidebar" />
    </button>
  </div>
  <div v-if="filterShown" class="bar">
    <div class="search" style="width: 100%">
      <Icon name="search" />
      <input ref="filterInput" v-model="ui.feedFilter" class="input" placeholder="Filter feeds…" @keydown.esc="(ui.feedFilter = ''), filterInput?.blur()" />
      <button v-if="ui.feedFilter" class="btn icon-only clear" title="Clear" @click="ui.feedFilter = ''"><Icon name="x" /></button>
    </div>
  </div>

  <div ref="scroller" class="sidebar-scroll">
    <div v-if="showAllRow" class="tree-row" :class="{ selected: stories.scope === 'all' || (mode === 'saved' && stories.scope === 'saved') }" @click="openAll()">
      <Icon :name="mode === 'saved' ? 'bookmark' : 'stack'" />
      <span class="title">{{ mode === 'saved' ? 'All Saved Stories' : 'All Site Stories' }}</span>
      <span v-if="allCount > 0" class="count static" :class="{ saved: mode === 'saved' }">{{ allCount }}</span>
    </div>
    <div
      v-for="[tag, count] in mode === 'saved' ? savedTags : []" :key="tag" class="tree-row" style="padding-left: 14px"
      :class="{ selected: stories.scope === `saved:tag:${tag}` }" @click="openScope(`saved:tag:${tag}`)"
    >
      <Icon name="tag" style="width: 12px; height: 12px" />
      <span class="title">{{ tag }}</span>
      <span class="count saved static">{{ count }}</span>
    </div>

    <TreeNode :parent-id="null" :depth="0" />

    <template v-if="mode === 'saved'">
      <div
        v-for="orphan in tree.orphanSavedFeeds" :key="orphan.id" class="tree-row feed"
        :class="{ selected: stories.scope === `saved:feed:${orphan.id}` }" style="padding-left: 14px"
        @click="openScope(`saved:feed:${orphan.id}`)"
      >
        <span class="feed-icon placeholder"><Icon name="rss" style="width: 10px; height: 10px" /></span>
        <span class="title">{{ orphan.title }}</span>
        <span class="count saved static">{{ orphan.count }}</span>
      </div>
    </template>
    <div v-if="!tree.feeds.length && mode === 'all'" class="list-footer">No feeds yet. Use “Add” below to subscribe.</div>
    <div v-show="dropLine.visible" class="drop-line" :style="{ top: dropLine.top + 'px', left: dropLine.left + 'px', right: '6px' }" />
  </div>

  <div v-if="tree.unfetched > 0 && !tree.progressDismissed" class="sidebar-notice">
    <div style="display: flex; align-items: center">
      <span>Fetching your feeds — {{ fetchedCount }} of {{ tree.progressTotal }}</span>
      <span class="spacer" />
      <button class="btn icon-only" style="height: 18px; width: 18px" title="Dismiss" @click="tree.progressDismissed = true"><Icon name="x" /></button>
    </div>
    <div class="progress"><div :style="{ width: (tree.progressTotal ? (fetchedCount / tree.progressTotal) * 100 : 0) + '%' }" /></div>
  </div>
  <div v-if="!session.worker.alive" class="sidebar-notice warning">Feeds are not updating</div>

  <div class="bar bottom">
    <button class="btn" title="Add a site (A)" @click="ui.openDialog('add')"><Icon name="plus" /> Add</button>
    <button class="btn icon-only" title="Manage" @click="manageMenu"><Icon name="gear" /></button>
    <span class="spacer" />
    <div class="segmented">
      <button class="btn" :class="{ active: mode === 'all' }" @click="settings.set('sidebar_mode', 'all')">All</button>
      <button class="btn" :class="{ active: mode === 'unread' }" @click="settings.set('sidebar_mode', 'unread')">Unread</button>
      <button class="btn" :class="{ active: mode === 'saved' }" title="Saved for Later" @click="settings.set('sidebar_mode', 'saved')">
        <Icon name="bookmark" /> Saved
      </button>
    </div>
  </div>
  <div v-if="ghost.visible" class="drag-ghost" :style="{ left: ghost.x + 'px', top: ghost.y + 'px' }">{{ ghost.label }}</div>
</template>
