<!-- The content side of the shell (frontend/src/components/ContentArea.vue): top bar, the layout of
     the open scope and the right taskbar (items 9, 10, 20). -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  effectiveLayout, effectiveView, LAYOUTS, markScopeRead, scopeTitle, setView, stepStory, viewAvailable, VIEWS,
} from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import { useUi } from '../stores/ui'
import type { Layout, View, ViewKey } from '../types'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'
import StoryList from './StoryList.vue'
import StoryPane from './StoryPane.vue'
import StylePopover from './StylePopover.vue'

const stories = useStories()
const settings = useSettings()
const tree = useTree()
const ui = useUi()
const route = useRoute()
const router = useRouter()
const searchInput = ref<HTMLInputElement | null>(null)
const search = ref(stories.query)
let searchTimer: number | undefined

defineExpose({ focusSearch: () => searchInput.value?.focus() })

const scope = computed(() => stories.scope)
const layout = computed(() => effectiveLayout())
const view = computed(() => effectiveView())
const filter = computed(() => settings.effective(scope.value, 'read_filter'))
const order = computed(() => settings.effective(scope.value, 'order'))
const title = computed(() => scopeTitle(scope.value))
const feed = computed(() => (stories.feedId !== null ? tree.feedById.get(stories.feedId) : undefined))
const scopeCount = computed(() => {
  if (!scope.value) return 0
  if (stories.isSaved) return 0
  if (scope.value === 'all') return tree.totalUnread
  if (feed.value) return feed.value.unread
  const folder = scope.value.match(/^folder:(\d+)$/)
  return folder ? tree.folderFeedIds(Number(folder[1])).reduce((sum, id) => sum + (tree.feedById.get(id)?.unread ?? 0), 0) : 0
})
const anchor = computed(() => settings.prefs.story_pane_anchor)
const titlesStyle = computed(() => {
  const size = settings.prefs.story_titles_pane_size + 'px'
  return anchor.value === 'west' ? { width: size } : { height: size }
})
const LAYOUT_LABELS: Record<Layout, string> = { full: 'Full', split: 'Split', list: 'List', magazine: 'Magazine', grid: 'Grid' }
const VIEW_LABELS: Record<View, string> = { original: 'Original', feed: 'Feed', text: 'Text', story: 'Story' }
const READ_FILTERS = ['unread', 'all'] as const
const READ_FILTER_LABELS: Record<(typeof READ_FILTERS)[number], string> = { unread: 'Unread', all: 'All' }
const ORDERS = ['newest', 'oldest'] as const
const ORDER_LABELS: Record<(typeof ORDERS)[number], string> = { newest: 'Newest', oldest: 'Oldest' }

function overridden(key: ViewKey): boolean {
  return !!scope.value && settings.stored(scope.value, key) !== undefined
}

/** R19.3: a change is stored for the open scope; order and filter reload the list (R19.6). */
function setSetting(key: ViewKey, value: string) {
  if (!scope.value) return
  settings.setView(scope.value, key, value).then(() => {})
  if (key === 'order' || key === 'read_filter') stories.reload()
}
function useDefault(event: MouseEvent, key: ViewKey) {
  if (!scope.value) return
  ui.openMenu(event, [
    {
      label: 'Use default',
      icon: 'refresh',
      disabled: !overridden(key),
      action: () => {
        settings.clearView(scope.value!, key)
        if (key === 'order' || key === 'read_filter') stories.reload()
      },
    },
  ])
}

function markMenu(event: MouseEvent) {
  ui.openMenuAt(event.currentTarget as HTMLElement, [
    { label: 'Mark all as read', icon: 'check', action: () => markScopeRead(0, scope.value, { undoable: true }) },
    { label: '', separator: true },
    ...[1, 3, 7, 14].map((days) => ({ label: `Older than ${days} day${days === 1 ? '' : 's'}`, icon: 'clock', action: () => markScopeRead(days) })),
  ])
}

// ------------------------------------------------------------------ search (R12.6)

function applySearch(value: string) {
  clearTimeout(searchTimer)
  const query = value.trim().length >= 2 ? value.trim() : ''
  if (query === stories.query) return
  stories.setQuery(query)
  router.replace({ path: route.path, query: query ? { q: query } : {} })
}
function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => applySearch(search.value), 400)
}
function clearSearch() {
  search.value = ''
  applySearch('')
  searchInput.value?.blur()
}
watch(() => stories.query, (query) => {
  if (query !== search.value.trim()) search.value = query
})

// ------------------------------------------------------------------ titles pane divider

const dragging = ref(false)
function dragDivider(event: PointerEvent) {
  const start = anchor.value === 'west' ? event.clientX : event.clientY
  const initial = settings.prefs.story_titles_pane_size
  dragging.value = true
  const move = (e: PointerEvent) => {
    let delta = (anchor.value === 'west' ? e.clientX : e.clientY) - start
    if (anchor.value === 'south') delta = -delta
    settings.set('story_titles_pane_size', Math.max(68, Math.round(initial + delta)))
  }
  const up = () => {
    dragging.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
</script>

<template>
  <div class="content">
    <div class="bar">
      <button
        v-if="settings.prefs.sidebar_collapsed || ui.fullscreen" class="btn icon-only" title="Show the sidebar (Shift+U)"
        @click="(ui.fullscreen = false), settings.set('sidebar_collapsed', false)"
      ><Icon name="sidebar" /></button>
      <button class="btn icon-only" title="Previous story (K)" :disabled="!scope" @click="stepStory(-1)"><Icon name="chevron-left" /></button>
      <button class="btn icon-only" title="Next story (J)" :disabled="!scope" @click="stepStory(1)"><Icon name="chevron-right" /></button>
      <div class="scope-title">
        <template v-if="scope">
          <FeedIcon v-if="stories.feedId !== null" :feed-id="stories.feedId" />
          <Icon v-else-if="stories.isSaved" name="bookmark" />
          <Icon v-else-if="scope === 'all'" name="stack" />
          <Icon v-else name="folder" />
          <span>{{ title }}</span>
          <span v-if="scopeCount > 0" class="count static">{{ scopeCount }}</span>
        </template>
      </div>
      <template v-if="scope">
        <div class="popover-anchor">
          <button
            class="btn icon-only" :class="{ active: ui.stylePopover === 'list' }" title="List style" data-style-toggle
            @click="ui.stylePopover = ui.stylePopover === 'list' ? null : 'list'"
          ><Icon name="list" /></button>
          <StylePopover v-if="ui.stylePopover === 'list'" part="list" class="below" @close="ui.stylePopover = null" />
        </div>
        <div
          class="segmented" role="group" aria-label="Read filter"
          title="Show unread or all stories (Shift+L). Right-click: use default" @contextmenu.prevent="useDefault($event, 'read_filter')"
        >
          <button
            v-for="f in READ_FILTERS" :key="f" class="btn" :class="{ active: filter === f, override: filter === f && overridden('read_filter') }"
            :aria-pressed="filter === f" :disabled="stories.isSaved" @click="filter !== f && setSetting('read_filter', f)"
          >{{ READ_FILTER_LABELS[f] }}</button>
        </div>
        <div class="segmented" role="group" aria-label="Order" title="Order. Right-click: use default" @contextmenu.prevent="useDefault($event, 'order')">
          <button
            v-for="o in ORDERS" :key="o" class="btn" :class="{ active: order === o, override: order === o && overridden('order') }"
            :aria-pressed="order === o" @click="order !== o && setSetting('order', o)"
          >{{ ORDER_LABELS[o] }}</button>
        </div>
        <div v-if="!stories.isSaved" style="display: inline-flex">
          <button class="btn" title="Mark all as read (Shift+A)" @click="markScopeRead(0, scope, { undoable: true })"><Icon name="check" /> Mark read</button>
          <button class="btn icon-only" style="width: 18px" title="More" @click="markMenu"><Icon name="chevron-down" /></button>
        </div>
        <div class="search">
          <Icon name="search" />
          <input
            ref="searchInput" v-model="search" class="input" placeholder="Search…"
            @input="onSearchInput" @keydown.enter="applySearch(search)" @keydown.esc.stop="clearSearch"
          />
          <button v-if="search" class="btn icon-only clear" title="Clear" @click="clearSearch"><Icon name="x" /></button>
        </div>
      </template>
    </div>

    <div class="content-body">
      <div v-if="!scope" class="hint">Select a feed or folder</div>
      <template v-else-if="layout === 'split'">
        <div class="split" :class="anchor">
          <template v-if="!ui.fullscreen">
            <StoryList variant="titles" :style="titlesStyle" />
            <div class="divider" :class="[anchor === 'west' ? 'vertical' : 'horizontal', { dragging }]" @pointerdown.prevent="dragDivider" />
          </template>
          <StoryPane :full="false" />
        </div>
      </template>
      <StoryPane v-else-if="layout === 'full'" :full="true" />
      <StoryList v-else :variant="layout" />
    </div>

    <div class="bar bottom">
      <template v-if="scope">
        <div class="segmented" :class="{ override: overridden('view') }" title="View. Right-click: use default" @contextmenu.prevent="useDefault($event, 'view')">
          <button
            v-for="v in VIEWS" :key="v" class="btn" :class="{ active: view === v, override: view === v && overridden('view') }"
            :disabled="!viewAvailable(v)" @click="setView(v)"
          >{{ VIEW_LABELS[v] }}</button>
        </div>
        <span class="spacer" />
        <div class="popover-anchor">
          <button
            class="btn" :class="{ active: ui.stylePopover === 'story' }" title="Story style" data-style-toggle
            @click="ui.stylePopover = ui.stylePopover === 'story' ? null : 'story'"
          ><Icon name="sliders" /> Style</button>
          <StylePopover v-if="ui.stylePopover === 'story'" part="story" @close="ui.stylePopover = null" />
        </div>
        <div class="segmented" title="Layout. Right-click: use default" @contextmenu.prevent="useDefault($event, 'layout')">
          <button
            v-for="l in LAYOUTS" :key="l" class="btn" :class="{ active: layout === l, override: layout === l && overridden('layout') }"
            :title="LAYOUT_LABELS[l]" @click="setSetting('layout', l)"
          ><Icon :name="l" /> {{ LAYOUT_LABELS[l] }}</button>
        </div>
      </template>
    </div>
  </div>
</template>
