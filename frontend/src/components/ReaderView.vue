<!-- The application shell (frontend/src/components/ReaderView.vue): sidebar and content,
     route to scope synchronization, keyboard shortcuts, dialogs, menus and toasts. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { continueTarget, effectiveLayout, scopeTitle, selectStory } from '../actions'
import { scopeFromRoute, scopePath } from '../router'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import { useUi } from '../stores/ui'
import { subscribeUrlFromLocation } from '../utils/format'
import { lastScope, rememberScope } from '../utils/lastScope'
import { handleKey } from '../utils/shortcuts'
import ContentArea from './ContentArea.vue'
import ContextMenu from './ContextMenu.vue'
import AddDialog from './dialogs/AddDialog.vue'
import FeedFiltersDialog from './dialogs/FeedFiltersDialog.vue'
import FeedSettingsDialog from './dialogs/FeedSettingsDialog.vue'
import PreferencesDialog from './dialogs/PreferencesDialog.vue'
import SimpleDialogs from './dialogs/SimpleDialogs.vue'
import StatisticsDialog from './dialogs/StatisticsDialog.vue'
import Sidebar from './Sidebar.vue'
import Toasts from './Toasts.vue'

const route = useRoute()
const router = useRouter()
const stories = useStories()
const settings = useSettings()
const tree = useTree()
const ui = useUi()
const sidebar = ref<InstanceType<typeof Sidebar> | null>(null)
const content = ref<InstanceType<typeof ContentArea> | null>(null)
const draggingSidebar = ref(false)

const sidebarVisible = computed(() => !settings.prefs.sidebar_collapsed && !ui.fullscreen)

// ------------------------------------------------------------------ route -> scope

/** Only the first navigation applies the start page preference; later visits to `/` show the page. */
let firstSync = true

/** The scope to open instead of the start page when the app opens at `/` (`start_page`). */
function startupScope(): string | null {
  const choice = settings.prefs.start_page
  if (choice === 'all') return 'all'
  if (choice === 'last') return continueTarget(lastScope())?.scope ?? null
  return null
}

function sync() {
  if (route.name === 'subscribe') {
    // Everything after /subscribe/ in the raw location, query string included, is the URL.
    const url = subscribeUrlFromLocation(window.location.pathname + window.location.search)
    router.replace('/')
    if (url) ui.openDialog('add', { url })
    return
  }
  if (route.name === 'login' || route.name === 'unknown') {
    router.replace('/')
    return
  }
  const startup = firstSync
  firstSync = false
  const scope = scopeFromRoute(route)
  if (!scope && startup && route.name === 'home') {
    const start = startupScope()
    if (start) {
      router.replace(scopePath(start))
      return
    }
  }
  const query = typeof route.query.q === 'string' ? route.query.q : ''
  // Hidden stories are shown only in a single feed.
  const showHidden = route.query.hidden === '1' && scope !== null && scope.startsWith('feed:')
  const storyId = route.params.storyId ? Number(route.params.storyId) : null
  if (!scope) {
    stories.close()
    return
  }
  if (scope !== stories.scope) {
    if (!query) rememberScope(scope)
    stories.open(scope, { storyId, query, showHidden }).then(() => {
      // `newest` selects the first story when a scope opens in Split layout.
      const first = stories.stories[0]
      const auto = settings.prefs.open_feed_action === 'newest' && effectiveLayout() === 'split' && !stories.isSaved
      if (auto && !storyId && first && stories.scope === scope && !stories.selected) selectStory(first)
    })
    return
  }
  if (query !== stories.query) stories.setQuery(query)
  stories.setShowHidden(showHidden)
  if (storyId && stories.selected?.id !== storyId) stories.selectById(storyId)
}
watch(() => route.fullPath, sync, { immediate: true })

// ------------------------------------------------------------------ window

function onKey(event: KeyboardEvent) {
  handleKey(event, {
    focusFilter: () => {
      if (settings.prefs.sidebar_collapsed) settings.set('sidebar_collapsed', false)
      ui.fullscreen = false
      setTimeout(() => sidebar.value?.focusFilter(), 0)
    },
    focusSearch: () => content.value?.focusSearch(),
  })
}
function onResize() {
  ui.narrow = window.innerWidth < 900
}
function onBeforeUnload() {
  settings.flush()
  stories.flushRead()
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  window.addEventListener('resize', onResize)
  window.addEventListener('beforeunload', onBeforeUnload)
  if (ui.narrow && !settings.prefs.sidebar_collapsed) ui.fullscreen = true // starts collapsed below 900 px
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('beforeunload', onBeforeUnload)
})

// "(N) <scope title>" in the browser tab.
watchEffect(() => {
  const title = scopeTitle(stories.scope)
  const count = tree.totalUnread
  document.title = settings.prefs.title_counts && count > 0 ? `(${count}) ${title}` : title
})

// The sidebar is resizable, at least 225 px wide.
function dragSidebar(event: PointerEvent) {
  const start = event.clientX
  const initial = settings.prefs.feed_pane_size
  draggingSidebar.value = true
  const move = (e: PointerEvent) => settings.set('feed_pane_size', Math.max(225, Math.min(900, Math.round(initial + e.clientX - start))))
  const up = () => {
    draggingSidebar.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
</script>

<template>
  <div class="app">
    <aside v-show="sidebarVisible" class="sidebar" :style="{ width: settings.prefs.feed_pane_size + 'px' }">
      <Sidebar ref="sidebar" />
    </aside>
    <div v-show="sidebarVisible" class="divider vertical" :class="{ dragging: draggingSidebar }" @pointerdown.prevent="dragSidebar" />
    <ContentArea ref="content" />

    <template v-if="ui.dialog">
      <AddDialog v-if="ui.dialog.name === 'add'" :url="ui.dialog.props.url" :folder-id="ui.dialog.props.folderId" />
      <PreferencesDialog v-else-if="ui.dialog.name === 'preferences'" :initial-tab="ui.dialog.props.tab" />
      <FeedSettingsDialog v-else-if="ui.dialog.name === 'feed-settings'" :key="ui.dialog.props.feedId" :feed-id="ui.dialog.props.feedId" />
      <FeedFiltersDialog v-else-if="ui.dialog.name === 'feed-filters'" :key="ui.dialog.props.feedId" :feed-id="ui.dialog.props.feedId" />
      <StatisticsDialog v-else-if="ui.dialog.name === 'statistics'" :key="ui.dialog.props.feedId" :feed-id="ui.dialog.props.feedId" />
      <SimpleDialogs v-else :key="ui.dialog.name" :name="ui.dialog.name" :options="ui.dialog.props" />
    </template>
    <ContextMenu />
    <Toasts />
  </div>
</template>
