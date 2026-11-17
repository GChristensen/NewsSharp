<!-- The start page (frontend/src/components/StartPage.vue): what `/` shows when no scope is open. It
     answers "where do I go next": one main action, a way back to the last scope, unread folders ranked,
     the newest headlines, feeds that keep failing and a reminder of Saved for Later. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, isAbort } from '../api'
import { continueTarget, openFeed, openScope, scopeTitle, startReading, unreadGroups } from '../actions'
import { useTree } from '../stores/tree'
import { useUi } from '../stores/ui'
import type { Feed, Story } from '../types'
import { displayTitle, relativeTime } from '../utils/format'
import { lastScope } from '../utils/lastScope'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'

const GROUPS_SHOWN = 7
const LATEST_SHOWN = 8
const SAVED_TAGS_SHOWN = 4
const LATEST_REFRESH_MS = 1500 // unread counts change in bursts; refetch the headlines once they settle

const tree = useTree()
const ui = useUi()

// Relative times ("2 minutes ago") stay current while the page is open.
const clock = ref(Date.now() / 1000)
let clockTimer: number | undefined

const hasFeeds = computed(() => tree.feeds.length > 0)
const unreadFeeds = computed(() => tree.feeds.filter((f) => !f.muted && f.unread > 0).length)
const lastUpdate = computed(() => tree.feeds.reduce((latest, f) => Math.max(latest, f.last_update), 0))

const groups = computed(() => unreadGroups())
const shownGroups = computed(() => groups.value.slice(0, GROUPS_SHOWN))
const hiddenGroups = computed(() => groups.value.length - shownGroups.value.length)
const largestGroup = computed(() => groups.value[0]?.unread ?? 1)

const resume = computed(() => {
  const target = continueTarget(lastScope())
  return target && target.unread > 0 ? { ...target, title: scopeTitle(target.scope) } : null
})

/**
 * Feeds that keep failing. A single failed fetch is usually transient, so a feed is listed after its
 * second failure in a row, or at once when it has never been fetched successfully.
 */
const failing = computed(() =>
  tree.feeds
    .filter((f) => f.has_exception && (f.errors_since_good >= 2 || !f.fetched_once))
    .sort((a, b) => b.errors_since_good - a.errors_since_good),
)

const savedTags = computed(() =>
  Object.entries(tree.saved.tags)
    .sort((a, b) => b[1] - a[1])
    .slice(0, SAVED_TAGS_SHOWN),
)

const bookmarklet = computed(() => `javascript:location.href='${window.location.origin}/subscribe/'+location.href`)

function failureText(feed: Feed): string {
  const reason = feed.exception_message || `Error ${feed.exception_code}`
  const times = feed.errors_since_good > 1 ? `, ${feed.errors_since_good} fetches in a row` : ''
  return `${reason}${times}`
}

// ------------------------------------------------------------------ just in

const latest = ref<Story[]>([])
const latestError = ref('')
let latestController: AbortController | null = null
let latestTimer: number | undefined

async function loadLatest() {
  latestController?.abort()
  latestController = new AbortController()
  const params = new URLSearchParams({ scope: 'all', filter: 'unread', order: 'newest', limit: String(LATEST_SHOWN) })
  try {
    const page = await api<{ stories: Story[] }>('GET', `/api/stories?${params}`, undefined, { signal: latestController.signal })
    latest.value = page.stories
    latestError.value = ''
  } catch (error) {
    if (!isAbort(error)) latestError.value = 'The newest stories could not be loaded.'
  }
}

watch(
  () => tree.totalUnread,
  () => {
    clearTimeout(latestTimer)
    latestTimer = window.setTimeout(loadLatest, LATEST_REFRESH_MS)
  },
)

onMounted(() => {
  if (tree.totalUnread > 0) loadLatest()
  clockTimer = window.setInterval(() => (clock.value = Date.now() / 1000), 30000)
})
onBeforeUnmount(() => {
  latestController?.abort()
  clearTimeout(latestTimer)
  clearInterval(clockTimer)
})

function feedTitle(feedId: number): string {
  return tree.feedById.get(feedId)?.title ?? tree.saved.titles[feedId] ?? ''
}
</script>

<template>
  <div class="start">
    <div class="start-inner">
      <!-- No subscriptions yet: the page is an introduction. -->
      <section v-if="!hasFeeds" class="start-card start-welcome">
        <h2>Add your first sites</h2>
        <p class="dim">Subscribe to a site by its address, or bring your subscriptions from another reader.</p>
        <div class="start-actions">
          <button class="btn primary" @click="ui.openDialog('add')"><Icon name="plus" /> Add a site</button>
          <button class="btn outline" @click="ui.openDialog('opml')"><Icon name="tray-swap" /> Import OPML</button>
        </div>
        <p class="dim start-note">
          Or drag this link to your bookmarks bar and click it on any site to subscribe:
          <a :href="bookmarklet" class="start-bookmarklet" @click.prevent>Subscribe in NewsSharp</a>
        </p>
      </section>

      <template v-else>
        <!-- Summary and the one main action. -->
        <section class="start-summary">
          <div>
            <h2 v-if="tree.totalUnread > 0">
              {{ tree.totalUnread }} unread <span class="dim">in {{ unreadFeeds }} {{ unreadFeeds === 1 ? 'feed' : 'feeds' }}</span>
            </h2>
            <h2 v-else><Icon name="check-circle" /> All caught up</h2>
            <div class="dim start-meta">Last update {{ relativeTime(lastUpdate, clock) }}</div>
          </div>
          <span class="spacer" />
          <div v-if="tree.totalUnread > 0" class="start-go">
            <button class="btn primary start-button" title="Start reading (Enter)" @click="startReading()">
              Start reading <Icon name="chevron-right" />
            </button>
            <span class="dim start-meta">or press Enter</span>
          </div>
          <div v-else class="start-actions">
            <button class="btn outline" @click="openScope('all')"><Icon name="stack" /> All Site Stories</button>
            <button v-if="tree.saved.total > 0" class="btn outline" @click="openScope('saved')"><Icon name="bookmark" /> Saved for Later</button>
          </div>
        </section>

        <!-- Back to where reading stopped. -->
        <button v-if="resume" class="start-card start-resume" @click="openScope(resume.scope)">
          <Icon name="refresh" />
          <span class="dim">Continue</span>
          <FeedIcon v-if="resume.scope.startsWith('feed:')" :feed-id="Number(resume.scope.slice(5))" />
          <span class="start-resume-title">{{ resume.title }}</span>
          <span class="spacer" />
          <span class="count static">{{ resume.unread }}</span>
        </button>

        <div v-if="tree.totalUnread > 0" class="start-columns">
          <section class="start-card">
            <h3>Unread</h3>
            <ul class="start-list">
              <li v-for="group in shownGroups" :key="group.scope">
                <button class="start-row" @click="openScope(group.scope)">
                  <Icon v-if="group.scope.startsWith('folder:')" name="folder" />
                  <FeedIcon v-else :feed-id="group.feedIds[0]" />
                  <span class="start-row-main">
                    <span class="start-row-line">
                      <span class="start-row-title">{{ group.title }}</span>
                      <span v-if="group.scope.startsWith('folder:')" class="start-icons">
                        <FeedIcon v-for="id in group.feedIds.slice(0, 3)" :key="id" :feed-id="id" />
                      </span>
                    </span>
                    <span class="start-bar"><span :style="{ width: (group.unread / largestGroup) * 100 + '%' }" /></span>
                  </span>
                  <span class="start-row-count">{{ group.unread }}</span>
                </button>
              </li>
            </ul>
            <button v-if="hiddenGroups > 0" class="btn start-more" @click="openScope('all')">
              {{ hiddenGroups }} more — All Site Stories <Icon name="chevron-right" />
            </button>
          </section>

          <section class="start-card">
            <h3>Just in</h3>
            <div v-if="latestError" class="error-text start-meta">{{ latestError }}</div>
            <ul class="start-list">
              <li v-for="story in latest" :key="story.id ?? story.saved_id ?? story.permalink">
                <button class="start-row start-story" :class="{ read: story.read }" @click="openScope('all', story.id)">
                  <FeedIcon :feed-id="story.feed_id" />
                  <span class="start-row-main">
                    <span class="start-row-title">{{ displayTitle(story.title, story.preview, story.permalink) }}</span>
                    <span class="dim start-meta">{{ feedTitle(story.feed_id) }} · {{ relativeTime(story.story_date, clock) }}</span>
                  </span>
                </button>
              </li>
            </ul>
            <button class="btn start-more" @click="openScope('all')">All Site Stories <Icon name="chevron-right" /></button>
          </section>
        </div>

        <!-- Shown only when something is wrong. -->
        <section v-if="failing.length" class="start-card start-attention">
          <h3><Icon name="alert" /> Needs attention <span class="dim">({{ failing.length }})</span></h3>
          <ul class="start-list">
            <li v-for="feed in failing" :key="feed.id" class="start-row static">
              <FeedIcon :feed-id="feed.id" />
              <span class="start-row-main">
                <a href="#" class="start-row-title" @click.prevent="openFeed(feed)">{{ feed.title }}</a>
                <span class="dim start-meta">{{ failureText(feed) }} · last tried {{ relativeTime(feed.last_update, clock) }}</span>
              </span>
              <button class="btn outline" @click="ui.openDialog('feed-settings', { feedId: feed.id })">Settings</button>
            </li>
          </ul>
        </section>

        <!-- A reminder, not a destination: one line. -->
        <section v-if="tree.saved.total > 0" class="start-saved">
          <button class="btn" @click="openScope('saved')"><Icon name="bookmark" /> Saved for Later <span class="count saved static">{{ tree.saved.total }}</span></button>
          <button v-for="[tag, count] in savedTags" :key="tag" class="btn" @click="openScope(`saved:tag:${tag}`)">
            <Icon name="tag" /> {{ tag }} <span class="dim">{{ count }}</span>
          </button>
        </section>
      </template>
    </div>
  </div>
</template>
