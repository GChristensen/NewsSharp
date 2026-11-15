<!-- A story title row for Split and List (frontend/src/components/StoryRow.vue), R9.6-R9.8. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import type { Story } from '../types'
import { mediaFallback } from '../utils/media'
import { useStoryItem } from '../utils/useStoryItem'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'

const props = defineProps<{ story: Story; selected: boolean; list: boolean; narrowPane: boolean }>()
const settings = useSettings()
const stories = useStories()
const item = useStoryItem(() => props.story, false)

const thumbSetting = computed(() => settings.effective(stories.scope, 'image_preview'))
const cover = computed(() => props.story.image_urls[0] ?? null)
// R9.8: without an image no thumbnail in Split with a west anchor; otherwise the space is kept.
const showThumb = computed(() => thumbSetting.value !== 'none' && (cover.value !== null || !props.narrowPane))
const thumbClass = computed(() => (thumbSetting.value.startsWith('large') ? 'large' : 'small'))
</script>

<template>
  <div
    class="story-row"
    :class="{ read: item.dimmed.value, selected, 'list-row': list, 'thumb-right': thumbSetting.endsWith('right'), 'one-line': list && !item.previewHtml.value }"
    :data-key="story.key" :data-unread="!story.read && !stories.isSaved ? '1' : undefined"
  >
    <span class="strip" :class="{ hidden: !stories.multiFeed }" :style="stories.multiFeed ? item.stripStyle.value : {}" />
    <span class="dot" :style="item.dotStyle.value" />
    <div v-if="showThumb" class="thumb" :class="thumbClass">
      <img v-if="cover" :src="cover" alt="" loading="lazy" referrerpolicy="no-referrer" @error="mediaFallback($event, story.image_sigs)" />
    </div>
    <div class="main">
      <div class="line">
        <FeedIcon v-if="stories.multiFeed" :feed-id="story.feed_id" style="align-self: center" />
        <span class="story-title" v-html="item.titleHtml.value" />
        <span v-if="list && story.author" class="author">{{ story.author }}</span>
        <span class="spacer" />
        <Icon v-if="story.saved && !stories.isSaved" name="bookmark" class="saved-mark" style="width: 12px; height: 12px" />
        <span class="meta">{{ item.date.value }}</span>
      </div>
      <div v-if="item.previewHtml.value" class="preview" v-html="item.previewHtml.value" />
    </div>
  </div>
</template>
