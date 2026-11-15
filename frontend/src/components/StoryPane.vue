<!-- The story pane of Split and Full (frontend/src/components/StoryPane.vue), item 9: one story, or
     all loaded stories one after another; the Original view frames the feed's site (R9.2). -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { effectiveView, selectStory } from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import type { Story } from '../types'
import { usePaneScroll } from '../utils/usePaneScroll'
import ListFooter from './ListFooter.vue'
import StoryDetail, { isFrameable } from './StoryDetail.vue'

const props = defineProps<{ full: boolean }>()
const stories = useStories()
const settings = useSettings()
const tree = useTree()
const pane = ref<HTMLElement | null>(null)
const siteFrameable = ref<boolean | null>(null)
let selectedHere = false

const view = computed(() => effectiveView())
const siteLink = computed(() => (stories.feedId !== null ? tree.feedById.get(stories.feedId)?.feed_link ?? '' : ''))
const originalFramed = computed(() => view.value === 'original' && siteFrameable.value === true)
/** Feed view lists every loaded story unless feed_view_single_story is on; Full always does. */
const allMode = computed(() => props.full || ((view.value === 'feed' || view.value === 'original') && !settings.prefs.feed_view_single_story))
const position = computed(() => (settings.prefs.full_width_story ? 'stretch' : settings.prefs.story_position))
const storyFramed = computed(() => !allMode.value && view.value === 'story')
const { onScroll } = usePaneScroll(pane, () => props.full && allMode.value)

function detailView(story: Story): 'feed' | 'text' | 'story' {
  const selected = stories.selectedKey === story.key
  if (view.value === 'original') return selected && siteFrameable.value === false ? 'text' : 'feed'
  if (view.value === 'feed') return 'feed'
  return selected || !allMode.value ? view.value : 'feed'
}

function clickStory(story: Story) {
  if (stories.selectedKey === story.key) return
  selectedHere = true
  selectStory(story)
}

watch(
  () => [view.value, siteLink.value],
  async () => {
    siteFrameable.value = null
    if (view.value !== 'original') return
    const link = siteLink.value
    const ok = link ? await isFrameable(link) : false
    if (link === siteLink.value) siteFrameable.value = ok
  },
  { immediate: true },
)

watch(
  () => stories.selectedKey,
  (key) => {
    const fromPane = selectedHere
    selectedHere = false
    if (!key || fromPane) return
    nextTick(() => {
      if (!pane.value) return
      if (allMode.value) pane.value.querySelector(`[data-key="${key}"]`)?.scrollIntoView({ block: 'start' })
      else pane.value.scrollTop = 0
    })
  },
)
</script>

<template>
  <div ref="pane" class="story-pane" :class="[`pos-${position}`, { framed: originalFramed || storyFramed }]" @scroll.passive="onScroll">
    <iframe
      v-if="originalFramed" class="story-frame" :src="siteLink"
      sandbox="allow-scripts allow-same-origin allow-popups allow-forms" referrerpolicy="no-referrer"
    />
    <template v-else>
      <div v-if="view === 'original' && siteFrameable === false" class="notice" style="margin: 12px 22px">
        This site cannot be embedded.
        <a v-if="siteLink" :href="siteLink" target="_blank" rel="noopener noreferrer">Open the site</a>
      </div>
      <template v-if="allMode">
        <button v-if="full && stories.newCount > 0" class="new-stories" @click="stories.reload()">
          {{ stories.newCount }} new {{ stories.newCount === 1 ? 'story' : 'stories' }}
        </button>
        <StoryDetail
          v-for="story in stories.stories" :key="story.key" :story="story" :view="detailView(story)"
          :show-feed-strip="stories.multiFeed" @click="clickStory(story)"
        />
        <ListFooter />
      </template>
      <StoryDetail
        v-else-if="stories.selected" :key="stories.selected.key" :story="stories.selected" :view="detailView(stories.selected)"
        :show-feed-strip="stories.multiFeed" :fill="true"
      />
      <div v-else class="hint" style="padding: 60px; text-align: center">Select a story</div>
    </template>
  </div>
</template>
