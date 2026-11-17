<!-- One story: header and body in Feed, Text or Story view (frontend/src/components/StoryDetail.vue),
     R9.2-R9.4, R9.11, item 13. Story view falls back to Text when the site cannot be framed. -->
<script lang="ts">
import { api } from '../api'
import type { TextResult } from '../types'

const textCache = new Map<string, TextResult>()
const frameCache = new Map<string, boolean>()

/** R9.2: ask the server whether a URL may be shown in an iframe. */
export async function isFrameable(url: string): Promise<boolean> {
  if (!/^https?:\/\//.test(url)) return false
  const cached = frameCache.get(url)
  if (cached !== undefined) return cached
  try {
    const result = await api<{ frameable: boolean }>('GET', `/api/frameable?url=${encodeURIComponent(url)}`)
    frameCache.set(url, result.frameable)
    return result.frameable
  } catch {
    return false
  }
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { copyLink, openFeed, openInNewTab } from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import { type MenuItem, useUi } from '../stores/ui'
import type { Story } from '../types'
import { feedColors } from '../utils/colors'
import { displayTitle, storyDate } from '../utils/format'
import { mediaFallback } from '../utils/media'
import { selectedServices, share, shareInput } from '../utils/share'
import FeedIcon from './FeedIcon.vue'
import Icon from './Icon.vue'

/** bare: only the content, without the feed strip, header, controls and notices (story_display "images").
 *  collapsible: a click on the empty margin above the title emits collapse (an inline story, as in NewsBlur). */
const props = defineProps<{
  story: Story; view: 'feed' | 'text' | 'story'; showFeedStrip: boolean; fill?: boolean; bare?: boolean; collapsible?: boolean
}>()
const emit = defineEmits<{ collapse: [] }>()
const settings = useSettings()
const stories = useStories()
const tree = useTree()
const ui = useUi()

const contentEl = ref<HTMLElement | null>(null)
const text = ref<TextResult | null>(null)
const textLoading = ref(false)
const frameable = ref<boolean | null>(null)
const tagEditor = ref(false)
const tagText = ref('')

const feed = computed(() => tree.feedById.get(props.story.feed_id))
const title = computed(() => displayTitle(props.story.title, props.story.preview, props.story.permalink))
const date = computed(() => storyDate(props.story.story_date, settings.prefs.timezone, settings.hour12))
const stripStyle = computed(() => feedColors(feed.value?.favicon_color, settings.dark)?.barStyle ?? {})

/** What is actually shown: Story view needs a frameable site, otherwise Text (decision 13). */
const mode = computed<'feed' | 'text' | 'frame' | 'checking'>(() => {
  if (props.view === 'story') {
    if (frameable.value === null) return 'checking'
    return frameable.value ? 'frame' : 'text'
  }
  return props.view
})
const framed = computed(() => mode.value === 'frame')
const body = computed(() => {
  if (mode.value === 'text' && text.value && !text.value.failed && text.value.text) return text.value.text
  return props.story.content ?? ''
})
const notice = computed(() => {
  const parts: string[] = []
  if (props.view === 'story' && frameable.value === false) parts.push('This site cannot be embedded.')
  if (mode.value === 'text' && text.value?.failed) parts.push("Text could not be extracted — showing the feed's version.")
  return parts.join(' ')
})

function textKey(): string {
  return props.story.key
}

async function loadText(force = false) {
  const key = textKey()
  if (!force && textCache.has(key)) {
    text.value = textCache.get(key)!
    return
  }
  const useSaved = stories.isSaved || props.story.id === null
  const url = useSaved ? `/api/saved/${props.story.saved_id}/text` : `/api/stories/${props.story.id}/text`
  textLoading.value = true
  text.value = null
  try {
    const result = await api<TextResult>('GET', url + (force ? '?force=1' : ''), undefined, { timeout: 40000 })
    if (key !== textKey()) return
    if (!result.failed) textCache.set(key, result)
    text.value = result
  } catch (error) {
    if (key === textKey()) text.value = { text: null, url: props.story.permalink, failed: true, reason: String(error) }
  } finally {
    if (key === textKey()) textLoading.value = false
  }
}

async function prepare() {
  tagEditor.value = false
  text.value = null
  frameable.value = null
  if (props.view === 'story') {
    const key = textKey()
    const ok = await isFrameable(props.story.permalink)
    if (key !== textKey()) return
    frameable.value = ok
    if (!ok) loadText()
  } else if (props.view === 'text') {
    loadText()
  }
}

watch(() => [props.story.key, props.view], prepare)
onMounted(prepare)
// R14.3: error events do not bubble, so the listener captures them on the content container.
watch(contentEl, (el) => el?.addEventListener('error', (event) => mediaFallback(event, props.story.image_sigs), true))

function openLink(href: string) {
  if (settings.prefs.new_window) openInNewTab(href)
  else window.location.href = href
}

function onContentClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  const link = target.closest('a')
  if (link && link.getAttribute('href')) {
    if (!settings.prefs.new_window) {
      event.preventDefault()
      window.location.href = link.getAttribute('href')!
    }
    return
  }
  // Without a title to click, an image outside a link opens the story.
  if (props.bare && target.closest('img, picture') && props.story.permalink) openLink(props.story.permalink)
}

/** The share menu of the services chosen in Preferences, with a link to choose them. */
function openShareMenu(event: MouseEvent) {
  const input = shareInput(props.story, body.value)
  const items: MenuItem[] = selectedServices(settings.prefs.share_services).map((service) => ({
    label: service.label,
    icon: service.icon,
    action: () => {
      share(service, input)
      stories.markRead(props.story)
    },
  }))
  if (items.length) items.push({ label: '', separator: true })
  items.push({ label: 'Choose services…', icon: 'prefs-sliders', action: () => ui.openDialog('preferences', { tab: 'Sharing' }) })
  ui.openMenuAt(event.currentTarget as HTMLElement, items)
}

function onSave() {
  if (!props.story.saved) {
    stories.save(props.story)
    return
  }
  tagText.value = props.story.user_tags.join(', ')
  tagEditor.value = !tagEditor.value
}
function saveTags() {
  const tags = tagText.value.split(',').map((t) => t.trim()).filter(Boolean)
  stories.save(props.story, tags)
  tagEditor.value = false
}
function removeSaved() {
  tagEditor.value = false
  stories.unsave(props.story)
}
</script>

<template>
  <article
    class="story-detail" :class="{ read: story.read && !stories.isSaved && !bare, bare }" :style="framed && fill ? { height: '100%', padding: 0 } : {}"
    :data-key="story.key" :data-unread="!story.read && !stories.isSaved ? '1' : undefined"
  >
    <iframe
      v-if="framed" class="story-frame" :style="{ height: fill ? '100%' : '80vh' }" :src="story.permalink"
      sandbox="allow-scripts allow-same-origin allow-popups allow-forms" referrerpolicy="no-referrer"
    />
    <template v-else>
      <div v-if="showFeedStrip && !bare" class="story-feed-strip" :style="stripStyle" @click="feed && openFeed(feed)">
        <FeedIcon :feed-id="story.feed_id" />
        <span>{{ feed?.title ?? story.feed_title }}</span>
      </div>
      <div v-if="!bare" class="story-top-margin" :class="{ collapsible }" @click="collapsible && emit('collapse')" />
      <div class="story-column">
        <header v-if="!bare" class="story-header">
          <h1><a :href="story.permalink" target="_blank" rel="noopener noreferrer">{{ title }}</a></h1>
          <div v-if="story.author || story.tags.length" class="byline">
            <span v-if="story.author">{{ story.author }}</span>
            <span v-for="tag in story.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
          <div class="story-actions">
            <button
              v-if="story.id !== null || story.saved" class="btn" :class="{ active: story.saved }"
              :title="story.saved ? 'Saved for Later — edit tags or remove' : 'Save for later (S)'" @click="onSave"
            >
              <Icon name="bookmark" /> {{ story.saved ? 'Saved for Later' : 'Save for later' }}
            </button>
            <button v-if="!stories.isSaved && story.id !== null" class="btn" title="Mark read / unread (U)" @click="stories.toggleRead(story)">
              <Icon :name="story.read ? 'circle' : 'check'" /> {{ story.read ? 'Mark unread' : 'Mark read' }}
            </button>
            <button class="btn" title="Open in a new tab (Shift+V)" @click="openInNewTab(story.permalink)"><Icon name="external" /> Open</button>
            <button class="btn icon-only" title="Copy link" @click="copyLink(story.permalink)"><Icon name="copy" /></button>
            <button class="btn" title="Share" @click="openShareMenu"><Icon name="share" /> Share</button>
            <button v-if="mode === 'text' && text && !textLoading" class="btn" title="Extract the text again" @click="loadText(true)">
              <Icon name="refresh" /> Fetch again
            </button>
            <span class="story-date">{{ date }}</span>
            <div v-if="tagEditor" class="tag-popover" @keydown.esc.stop="tagEditor = false">
              <label class="dim">Tags, separated by commas</label>
              <input v-model="tagText" class="input" placeholder="later, tech" @keydown.enter="saveTags" />
              <div style="display: flex; gap: 6px">
                <button class="btn primary" @click="saveTags">Save tags</button>
                <span class="spacer" />
                <button class="btn danger" title="Remove from Saved for Later" @click="removeSaved"><Icon name="trash" /> Remove</button>
              </div>
            </div>
          </div>
        </header>
        <div v-if="notice && !bare" class="notice">
          {{ notice }} <a :href="story.permalink" target="_blank" rel="noopener noreferrer">Open the original</a>
        </div>
        <div v-if="((mode === 'text' && textLoading) || mode === 'checking') && !bare" class="notice">
          <span class="spinner" style="display: inline-block; vertical-align: -2px" />
          {{ mode === 'checking' ? 'Checking the site…' : 'Extracting the article text…' }}
        </div>
        <div ref="contentEl" class="story-content" :class="`font-${settings.prefs.story_styling}`" @click="onContentClick" v-html="body" />
      </div>
    </template>
  </article>
</template>
