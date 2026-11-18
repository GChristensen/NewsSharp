<!-- Theme preview (frontend/src/components/ThemePreview.vue): a static miniature of the reader in the
     Theme tab of the preferences, drawn with the same color tokens as the app, so that a theme and the
     color adjustments can be judged on bars, panes, lists, a story and a menu while they change. It is
     inert: nothing in it can be focused or clicked. -->
<script setup lang="ts">
import Icon from './Icon.vue'

interface SampleFeed {
  title: string
  unread: number
  selected?: boolean
  error?: boolean
}

interface SampleStory {
  title: string
  feed: string
  time: string
  unread: boolean
  selected?: boolean
  saved?: boolean
  summary?: string
}

const FEEDS: SampleFeed[] = [
  { title: 'Orbital Weekly', unread: 12, selected: true },
  { title: 'The Long Field', unread: 4 },
  { title: 'Quiet Compiler', unread: 0 },
  { title: 'Night Market Notes', unread: 27, error: true },
]

const STORIES: SampleStory[] = [
  { title: 'A small satellite finds an unusually cold ring', feed: 'Orbital Weekly', time: '09:42', unread: true, selected: true, saved: true, summary: 'Readings from two instruments agree on a ring ten degrees colder than its neighbours.' },
  { title: 'Launch window moves to the end of the month', feed: 'Orbital Weekly', time: '08:15', unread: true, summary: 'Ground crews need three more weeks for the fuel tests.' },
  { title: 'What a decade of ground stations taught us', feed: 'Orbital Weekly', time: 'Yesterday', unread: false },
  { title: 'Reading old telemetry with new tools', feed: 'Orbital Weekly', time: 'Yesterday', unread: false },
  { title: 'Letters: on naming small moons', feed: 'Orbital Weekly', time: 'Mon', unread: true },
]

const MENU: { label: string; icon: string; tone: string }[] = [
  { label: 'Mark as read', icon: 'check-circle', tone: 'read' },
  { label: 'Save for Later', icon: 'bookmark-plus', tone: 'saved' },
  { label: 'Refresh feed', icon: 'sync', tone: 'sync' },
  { label: 'Statistics', icon: 'bar-chart', tone: 'insight' },
  { label: 'Move to folder', icon: 'folder-add', tone: 'organize' },
  { label: 'Open the site', icon: 'globe', tone: 'web' },
]
</script>

<template>
  <div class="theme-preview" inert aria-hidden="true">
    <div class="tp-bar">
      <Icon name="rss" />
      <strong>Orbital Weekly</strong>
      <span class="tp-search"><Icon name="search" /> Search stories</span>
      <span class="spacer" />
      <span class="segmented">
        <span class="btn active"><Icon name="split" /></span>
        <span class="btn"><Icon name="list" /></span>
        <span class="btn"><Icon name="grid" /></span>
      </span>
      <span class="btn primary">Mark all read</span>
    </div>

    <div class="tp-body">
      <div class="tp-sidebar">
        <div class="tp-tree-row"><Icon name="stack" /><span class="tp-grow">All Site Stories</span><span class="count static">43</span></div>
        <div class="tp-tree-row"><Icon name="bookmark" /><span class="tp-grow">Saved for Later</span><span class="count saved static">8</span></div>
        <div class="tp-tree-row tp-folder"><Icon name="chevron-down" /><Icon name="folder" /><span class="tp-grow">Science</span></div>
        <div
          v-for="feed in FEEDS" :key="feed.title" class="tp-tree-row tp-feed"
          :class="{ selected: feed.selected, read: !feed.unread }"
        >
          <span class="feed-icon placeholder" />
          <span class="tp-grow">{{ feed.title }}</span>
          <span v-if="feed.error" class="feed-state"><Icon name="alert" /></span>
          <span v-if="feed.unread" class="count static">{{ feed.unread }}</span>
        </div>
        <div class="tp-tree-row tp-hover"><span class="feed-icon placeholder" /><span class="tp-grow">Hovered feed</span></div>
      </div>

      <div class="tp-list">
        <div v-for="story in STORIES" :key="story.title" class="tp-story" :class="{ selected: story.selected, read: !story.unread }">
          <span class="tp-dot" :class="{ on: story.unread }" />
          <div class="tp-grow">
            <div class="tp-story-title">{{ story.title }}</div>
            <div v-if="story.summary" class="tp-story-summary">{{ story.summary }}</div>
            <div class="tp-story-meta">{{ story.feed }} · {{ story.time }}</div>
          </div>
          <span v-if="story.saved" class="tp-saved"><Icon name="bookmark" /></span>
        </div>
      </div>

      <div class="tp-story-pane">
        <div class="tp-detail-meta">Orbital Weekly · Today 09:42 · by M. Ilves</div>
        <h2 class="tp-detail-title">A small satellite finds an unusually cold ring</h2>
        <p>
          The ring sits farther out than models predicted and is colder than its neighbours by almost
          ten degrees. <a href="#">The mission team's notes</a> describe how the readings were checked
          against two older instruments.
        </p>
        <blockquote>"We kept looking for the error, and it kept not being there."</blockquote>
        <p class="tp-read-text">Earlier coverage of the mission is <mark>marked by a search</mark> here, in the colors of read text.</p>
        <div class="tp-actions">
          <span class="btn outline"><Icon name="bookmark" /> Saved for Later</span>
          <span class="btn outline"><Icon name="share" /> Share</span>
          <span class="btn"><Icon name="external" /> Original</span>
        </div>
        <div class="tp-error"><Icon name="alert" /> Night Market Notes could not be fetched (HTTP 503).</div>

        <div class="tp-menu">
          <div v-for="item in MENU" :key="item.label" class="tp-menu-item" :class="{ 'tp-menu-hover': item.tone === 'saved' }">
            <span :style="{ color: `var(--tone-${item.tone})` }"><Icon :name="item.icon" /></span>
            {{ item.label }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
