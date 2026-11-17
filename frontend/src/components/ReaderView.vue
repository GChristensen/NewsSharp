<!-- The application shell (frontend/src/components/ReaderView.vue), item 20: sidebar and content,
     route to scope synchronization (R20.8), keyboard shortcuts (item 11), dialogs, menus and toasts. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { effectiveLayout, scopeTitle, selectStory } from '../actions'
import { scopeFromRoute } from '../router'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import { useUi } from '../stores/ui'
import { subscribeUrlFromLocation } from '../utils/format'
import { handleKey } from '../utils/shortcuts'
import ContentArea from './ContentArea.vue'
import ContextMenu from './ContextMenu.vue'
import AddDialog from './dialogs/AddDialog.vue'
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

function sync() {
  if (route.name === 'subscribe') {
    // R20.10: everything after /subscribe/ in the raw location, query string included, is the URL.
    const url = subscribeUrlFromLocation(window.location.pathname + window.location.search)
    router.replace('/')
    if (url) ui.openDialog('add', { url })
    return
  }
  if (route.name === 'login' || route.name === 'unknown') {
    router.replace('/')
    return
  }
  const scope = scopeFromRoute(route)
  const query = typeof route.query.q === 'string' ? route.query.q : ''
  const storyId = route.params.storyId ? Number(route.params.storyId) : null
  if (!scope) {
    stories.close()
    return
  }
  if (scope !== stories.scope) {
    stories.open(scope, { storyId, query }).then(() => {
      // R10.6: `newest` selects the first story when a scope opens in Split layout.
      const first = stories.stories[0]
      const auto = settings.prefs.open_feed_action === 'newest' && effectiveLayout() === 'split' && !stories.isSaved
      if (auto && !storyId && first && stories.scope === scope && !stories.selected) selectStory(first)
    })
    return
  }
  if (query !== stories.query) stories.setQuery(query)
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
  if (ui.narrow && !settings.prefs.sidebar_collapsed) ui.fullscreen = true // R20.9: starts collapsed below 900 px
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('beforeunload', onBeforeUnload)
})

// R20.11: "(N) <scope title>" in the browser tab.
watchEffect(() => {
  const title = scopeTitle(stories.scope)
  const count = tree.totalUnread
  document.title = settings.prefs.title_counts && count > 0 ? `(${count}) ${title}` : title
})

// R20.1: the sidebar is resizable, at least 225 px wide.
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
      <StatisticsDialog v-else-if="ui.dialog.name === 'statistics'" :key="ui.dialog.props.feedId" :feed-id="ui.dialog.props.feedId" />
      <SimpleDialogs v-else :key="ui.dialog.name" :name="ui.dialog.name" :options="ui.dialog.props" />
    </template>
    <ContextMenu />
    <Toasts />
  </div>
</template>
