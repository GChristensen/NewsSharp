<!-- A story card for Grid and Magazine (frontend/src/components/StoryCard.vue), R9.9, R9.10.
     Cover images and videos are contained, never cropped. A click anywhere on the card opens the story
     inline; a middle click opens the article (handled by StoryList). A Grid card with a video plays it
     muted (CardVideo): on hover over the cover image, or continuously when there is no image. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useStories } from '../stores/stories'
import type { Story } from '../types'
import { firstVideo, mediaFallback } from '../utils/media'
import { useStoryItem } from '../utils/useStoryItem'
import CardVideo from './CardVideo.vue'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'

const props = defineProps<{ story: Story; selected: boolean; magazine: boolean }>()
const stories = useStories()
const item = useStoryItem(() => props.story, true)
const cover = computed(() => props.story.image_urls[0] ?? null)
const video = computed(() => firstVideo(props.story.content))
const hovered = ref(false)
</script>

<template>
  <div
    :class="[magazine ? 'mag-card' : 'card', { read: item.dimmed.value, selected }]"
    :data-key="story.key" :data-unread="!story.read && !stories.isSaved ? '1' : undefined"
  >
    <span v-if="magazine" class="strip" :style="stories.multiFeed ? item.stripStyle.value : { background: 'transparent' }" />
    <div v-if="!magazine && stories.multiFeed" class="card-strip" :style="item.cardStripStyle.value" />
    <div v-if="!magazine" class="card-image" @mouseenter="hovered = true" @mouseleave="hovered = false">
      <img v-if="cover" :src="cover" alt="" loading="lazy" referrerpolicy="no-referrer" @error="mediaFallback($event, story.image_sigs)" />
      <CardVideo v-if="video" :src="video" :sigs="story.image_sigs" :autoplay="!cover" :hovered="hovered" />
    </div>
    <div v-else-if="cover || video" class="mag-image">
      <img v-if="cover" :src="cover" alt="" loading="lazy" referrerpolicy="no-referrer" @error="mediaFallback($event, story.image_sigs)" />
      <video v-else-if="video" :src="video" controls muted preload="metadata" @click.stop @error="mediaFallback($event, story.image_sigs)" />
    </div>
    <div v-if="!magazine" class="card-body">
      <div class="card-head">
        <div class="story-title" v-html="item.titleHtml.value" />
        <span class="card-date">{{ item.date.value }}</span>
      </div>
      <div v-if="stories.multiFeed || (story.saved && !stories.isSaved)" class="card-meta">
        <FeedIcon v-if="stories.multiFeed" :feed-id="story.feed_id" />
        <span v-if="stories.multiFeed" class="feed-name">{{ item.feedName.value }}</span>
        <span class="spacer" />
        <Icon v-if="story.saved && !stories.isSaved" name="bookmark" style="width: 12px; height: 12px" />
      </div>
      <div v-if="item.previewHtml.value" class="preview" v-html="item.previewHtml.value" />
    </div>
    <div v-else class="mag-body">
      <div class="story-title" v-html="item.titleHtml.value" />
      <div class="card-meta">
        <FeedIcon v-if="stories.multiFeed" :feed-id="story.feed_id" />
        <span v-if="stories.multiFeed" class="feed-name">{{ item.feedName.value }}</span>
        <span v-if="story.author">{{ story.author }}</span>
        <span class="spacer" />
        <Icon v-if="story.saved && !stories.isSaved" name="bookmark" style="width: 12px; height: 12px" />
        <span style="white-space: nowrap">{{ item.date.value }}</span>
      </div>
      <div v-if="item.previewHtml.value" class="preview" v-html="item.previewHtml.value" />
    </div>
  </div>
</template>
