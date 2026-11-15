<!-- The story list (frontend/src/components/StoryList.vue): the titles pane of Split and the single
     pane of List, Magazine and Grid, where the selected story opens inline (item 9). -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { copyLink, effectiveView, openInNewTab, selectStory } from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useUi, type MenuItem } from '../stores/ui'
import type { Story } from '../types'
import { usePaneScroll } from '../utils/usePaneScroll'
import ListFooter from './ListFooter.vue'
import StoryCard from './StoryCard.vue'
import StoryDetail from './StoryDetail.vue'
import StoryRow from './StoryRow.vue'

const props = defineProps<{ variant: 'titles' | 'list' | 'magazine' | 'grid' }>()
const stories = useStories()
const settings = useSettings()
const ui = useUi()
const pane = ref<HTMLElement | null>(null)

const inline = computed(() => props.variant !== 'titles')
const rows = computed(() => props.variant === 'titles' || props.variant === 'list')
const west = computed(() => props.variant === 'titles' && settings.prefs.story_pane_anchor === 'west')
const markOnScroll = computed(() => inline.value || settings.prefs.mark_read_on_scroll_titles)
const { onScroll } = usePaneScroll(pane, () => markOnScroll.value)
// R10.3: the end of the list is only reached once the last story has scrolled up to the top, so
// reaching it does not mark stories the user has not scrolled through (as NewsBlur pads its end line).
const endSpace = computed(() => markOnScroll.value && !stories.isSaved && stories.ended && stories.stories.length > 0)

const inlineView = computed(() => {
  const view = effectiveView()
  return view === 'original' ? 'feed' : view
})
const position = computed(() => (settings.prefs.full_width_story ? 'stretch' : settings.prefs.story_position))
const gridStyle = computed(() => {
  if (props.variant !== 'grid') return {}
  const columns = Number(settings.effective(stories.scope, 'grid_columns'))
  return { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }
})

function click(story: Story) {
  if (inline.value && stories.selectedKey === story.key) selectStory(null)
  else selectStory(story)
}

/** A middle click on a story that is not expanded inline opens its link in a new background tab. */
function middleClick(event: MouseEvent, story: Story) {
  if (event.button !== 1) return
  if (inline.value && stories.selectedKey === story.key) return
  event.preventDefault()
  openInNewTab(story.permalink, true)
}

/** Suppresses the browser's autoscroll on the middle-button press that precedes middleClick. */
function middleDown(event: MouseEvent, story: Story) {
  if (event.button === 1 && !(inline.value && stories.selectedKey === story.key)) event.preventDefault()
}

/** R10.8. */
function menu(event: MouseEvent, story: Story) {
  const items: MenuItem[] = []
  if (!stories.isSaved && story.id !== null) {
    items.push({ label: story.read ? 'Mark as unread' : 'Mark as read', icon: story.read ? 'circle' : 'check', action: () => stories.toggleRead(story) })
  }
  items.push({
    label: story.saved ? 'Remove from Saved for Later' : 'Save for later',
    icon: 'bookmark',
    action: () => (story.saved ? stories.unsave(story) : stories.save(story)),
  })
  if (!stories.isSaved) {
    items.push(
      { label: 'Mark newer stories as read', icon: 'arrow-up', action: () => stories.markRelative(story, 'newer') },
      { label: 'Mark older stories as read', icon: 'arrow-down', action: () => stories.markRelative(story, 'older') },
    )
  }
  items.push(
    { label: '', separator: true },
    { label: 'Open in new tab', icon: 'external', action: () => openInNewTab(story.permalink) },
    { label: 'Copy link', icon: 'copy', action: () => copyLink(story.permalink) },
  )
  ui.openMenu(event, items)
}

/** Keeps the selected story visible. In Grid the story opens in a full-width row below its card's
 *  row, so its top is scrolled to the top of the pane (as in NewsBlur). */
watch(
  () => stories.selectedKey,
  (key) => {
    if (!key) return
    nextTick(() => {
      if (props.variant === 'grid') pane.value?.querySelector('.inline-story')?.scrollIntoView({ block: 'start' })
      else pane.value?.querySelector(`[data-key="${key}"]`)?.scrollIntoView({ block: 'nearest' })
    })
  },
)
</script>

<template>
  <div
    ref="pane" :class="[variant === 'titles' ? 'titles-pane' : 'list-pane', { 'grid-pane': variant === 'grid' }]"
    :data-show-content-preview="settings.effective(stories.scope, 'show_content_preview')" @scroll.passive="onScroll"
  >
    <button v-if="stories.newCount > 0" class="new-stories" @click="stories.reload()">
      {{ stories.newCount }} new {{ stories.newCount === 1 ? 'story' : 'stories' }}
    </button>
    <div :class="{ grid: variant === 'grid' }" :style="gridStyle">
      <template v-for="story in stories.stories" :key="story.key">
        <StoryRow
          v-if="rows" :story="story" :selected="stories.selectedKey === story.key"
          :list="variant === 'list' || !west" :narrow-pane="west"
          @click="click(story)" @contextmenu.prevent="menu($event, story)"
          @mousedown="middleDown($event, story)" @auxclick="middleClick($event, story)"
        />
        <StoryCard
          v-else :story="story" :selected="stories.selectedKey === story.key" :magazine="variant === 'magazine'"
          @click="click(story)" @contextmenu.prevent="menu($event, story)"
          @mousedown="middleDown($event, story)" @auxclick="middleClick($event, story)"
        />
        <div v-if="inline && stories.selectedKey === story.key" class="inline-story" :class="`pos-${position}`">
          <StoryDetail :story="story" :view="inlineView" :show-feed-strip="stories.multiFeed" />
        </div>
      </template>
    </div>
    <ListFooter />
    <div v-if="endSpace" class="list-end-space" />
  </div>
</template>
