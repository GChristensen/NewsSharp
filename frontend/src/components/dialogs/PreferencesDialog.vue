<!-- Preferences dialog (frontend/src/components/dialogs/PreferencesDialog.vue): the settings page of
     item 19 with its General, Reading, Appearance, Sharing and Keyboard sections. -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api, errorMessage } from '../../api'
import { useSession } from '../../stores/session'
import { useSettings } from '../../stores/settings'
import { useStories } from '../../stores/stories'
import { useUi } from '../../stores/ui'
import type { DarkTint, Prefs, ViewKey } from '../../types'
import { SHARE_SERVICES } from '../../utils/share'
import Modal from '../Modal.vue'

interface Field {
  key: keyof Prefs
  label: string
  type: 'bool' | 'select' | 'number' | 'text' | 'tint'
  options?: [string, string][]
  min?: number
  max?: number
  /** A further check of a number within min and max. */
  valid?: (value: number) => boolean
  hint?: string
}

const SIZES: [string, string][] = [['xs', 'Extra small'], ['s', 'Small'], ['m', 'Medium'], ['l', 'Large'], ['xl', 'Extra large']]
const TABS: Record<string, Field[]> = {
  General: [
    { key: 'dateformat', label: 'Time format', type: 'select', options: [['24', '24 hours'], ['12', '12 hours']] },
    { key: 'timezone', label: 'Time zone', type: 'text', hint: 'An IANA zone such as Europe/Berlin' },
    { key: 'feedxcavator_url', label: 'feedxcavator address', type: 'text', hint: 'Shows a button in the sidebar header when set' },
    {
      key: 'feedxcavator_newspaper_url', label: 'feedxcavator news address', type: 'text',
      hint: 'Shows a newspaper button that sends a GET request to this address',
    },
  ],
  Reading: [
    { key: 'story_layout', label: 'Default layout', type: 'select', options: [['split', 'Split'], ['full', 'Full'], ['list', 'List'], ['magazine', 'Magazine'], ['grid', 'Grid']] },
    { key: 'default_view', label: 'Default view', type: 'select', options: [['feed', 'Feed'], ['text', 'Text'], ['story', 'Story'], ['original', 'Original']] },
    { key: 'default_order', label: 'Default order', type: 'select', options: [['newest', 'Newest first'], ['oldest', 'Oldest first']] },
    { key: 'default_read_filter', label: 'Default read filter (feeds)', type: 'select', options: [['all', 'All stories'], ['unread', 'Unread only']] },
    { key: 'grid_columns', label: 'Default stories per row (Grid)', type: 'number', min: 1, max: 6 },
    { key: 'story_display', label: 'Default story display (Full)', type: 'select', options: [['standard', 'Standard'], ['images', 'Content only']] },
    { key: 'days_of_unread', label: 'Days a story stays unread', type: 'number', min: 1, max: 365 },
    { key: 'read_story_delay', label: 'Mark a selected story read after (seconds)', type: 'number', min: -1, max: 60, hint: '0 = at once, -1 = never' },
    { key: 'mark_read_on_scroll_titles', label: 'Mark stories read when scrolled past (Split)', type: 'bool' },
    { key: 'start_page', label: 'When NewsSharp opens', type: 'select', options: [['home', 'Show the start page'], ['all', 'Open All Site Stories'], ['last', 'Open the last feed or folder']] },
    { key: 'open_feed_action', label: 'When a feed opens (Split)', type: 'select', options: [['newest', 'Select the first story'], ['none', 'Select nothing']] },
    { key: 'markread_nextfeed', label: 'After marking a feed read', type: 'select', options: [['nextfeed', 'Open the next unread feed'], ['nothing', 'Stay']] },
    { key: 'mark_read_river_confirm', label: 'Confirm marking All Site Stories read', type: 'bool' },
    { key: 'new_window', label: 'Open story links in a new tab', type: 'bool' },
  ],
  Appearance: [
    {
      key: 'dark_tint', label: 'Theme', type: 'tint', hint: 'Hover a square to preview it',
      options: [
        ['light', 'Light'], ['graphite', 'Graphite, blue accent'], ['ink', 'Ink, blue accent'],
        ['navy', 'Midnight navy, periwinkle accent'], ['warm', 'Warm charcoal, amber accent'], ['moss', 'Moss, green accent'],
        ['teal', 'Deep teal, cyan accent'], ['plum', 'Plum, violet accent'], ['ember', 'Ember, coral accent'],
        ['rose', 'Rosé, pink accent'], ['dim', 'Dim grey, soft blue accent'],
        ['oled', 'OLED black, white accent'],
      ],
    },
    { key: 'feed_size', label: 'Feed list and title size', type: 'select', options: SIZES },
    { key: 'show_content_preview', label: 'Default content preview', type: 'select', options: [['title', 'Title only'], ['small', 'Small'], ['medium', 'Medium'], ['large', 'Large']] },
    { key: 'image_preview', label: 'Default image preview', type: 'select', options: [['none', 'No image'], ['small-left', 'Small, left'], ['large-left', 'Large, left'], ['large-right', 'Large, right'], ['small-right', 'Small, right']] },
    { key: 'headline_indent', label: 'Default headline indent, px (List, Magazine)', type: 'number', min: 0, max: 400 },
    {
      key: 'summary_max_width', label: 'Default summary width, px (List, Magazine)', type: 'number', min: 0, max: 1600,
      valid: (value) => value === 0 || value >= 300, hint: '0 = no limit, otherwise 300 to 1600',
    },
    { key: 'show_all_site_stories', label: 'All Site Stories in the sidebar', type: 'bool' },
    { key: 'folder_counts', label: 'Unread counts on folders', type: 'bool' },
    { key: 'title_counts', label: 'Unread count in the browser tab title', type: 'bool' },
    { key: 'animations', label: 'Animations', type: 'bool' },
    { key: 'show_tooltips', label: 'Tooltips', type: 'bool' },
  ],
  // No field rows: the tab is the checkbox list of share services in the template.
  Sharing: [],
  Keyboard: [
    { key: 'keyboard_verticalarrows', label: 'Up / Down arrows', type: 'select', options: [['story', 'Previous / next story'], ['scroll', 'Scroll the story']] },
    { key: 'keyboard_horizontalarrows', label: 'Left / Right arrows', type: 'select', options: [['view', 'Switch view'], ['site', 'Previous / next feed']] },
    { key: 'space_bar_action', label: 'Space at the end of a story', type: 'select', options: [['next_unread', 'Next unread story'], ['scroll_only', 'Only scroll']] },
    { key: 'space_scroll_spacing', label: 'Space scrolls (% of the pane)', type: 'number', min: 10, max: 100 },
    { key: 'arrow_scroll_spacing', label: 'Arrows scroll (px)', type: 'number', min: 10, max: 500 },
  ],
}

const props = defineProps<{ initialTab?: string }>()
const settings = useSettings()
const session = useSession()
const ui = useUi()
const tab = ref(props.initialTab && props.initialTab in TABS ? props.initialTab : 'General')
const instance = ref<{ public_base_url: string; manual: boolean; current_origin: string } | null>(null)
const publicUrl = ref('')
const message = ref('')

/** A square of the theme picker: the light theme, or a tint of the dark theme. */
type ThemeChoice = 'light' | DarkTint

/** The framed square. "Follow the system" (set from the sidebar menu) frames the tint it uses when dark. */
function isChosen(choice: ThemeChoice): boolean {
  const light = settings.prefs.theme === 'light'
  return choice === 'light' ? light : !light && settings.prefs.dark_tint === choice
}

/** Show a theme on the whole page while its square is hovered; leaving the row or closing the dialog
    restores the saved preferences. */
function previewTheme(choice: ThemeChoice) {
  const root = document.documentElement
  root.dataset.theme = choice === 'light' ? 'light' : 'dark'
  if (choice !== 'light') root.dataset.tint = choice
}

function chooseTheme(choice: ThemeChoice) {
  if (choice === 'light') {
    settings.set('theme', 'light')
  } else {
    settings.set('dark_tint', choice)
    settings.set('theme', 'dark')
  }
  previewTheme(choice) // keep showing it while the pointer stays on the square
}

onUnmounted(() => settings.applyTheme())

function update(field: Field, event: Event) {
  const input = event.target as HTMLInputElement
  let value: any = input.value
  if (field.type === 'bool') value = input.checked
  else if (field.type === 'number') {
    value = Math.round(Number(input.value))
    if (!Number.isFinite(value) || value < field.min! || value > field.max! || (field.valid && !field.valid(value))) return
  } else if (field.key === 'timezone') {
    try {
      new Intl.DateTimeFormat('en', { timeZone: value })
    } catch {
      return
    }
  } else if ((field.key === 'feedxcavator_url' || field.key === 'feedxcavator_newspaper_url') && value && !/^https?:\/\//.test(value)) {
    return
  }
  settings.set(field.key, value)
}

/** Turn one share service on or off; the list is kept in catalog order. */
function toggleShare(key: string, on: boolean) {
  const chosen = new Set(settings.prefs.share_services)
  if (on) chosen.add(key)
  else chosen.delete(key)
  settings.set('share_services', SHARE_SERVICES.map((service) => service.key).filter((k) => chosen.has(k)))
}

/** R19.4: reset a per-scope setting for all scopes. */
async function reset(key: ViewKey, label: string) {
  try {
    await settings.resetAll(key)
    if (key === 'order') useStories().reload()
    message.value = `${label} was reset for all feeds and folders.`
  } catch (error) {
    message.value = errorMessage(error)
  }
}

async function savePublicUrl(value: string | null) {
  try {
    instance.value = await api('PUT', '/api/instance', { public_base_url: value })
    publicUrl.value = instance.value!.public_base_url
    message.value = 'The address was saved; push subscriptions are being renewed.'
  } catch (error) {
    message.value = errorMessage(error)
  }
}

onMounted(async () => {
  if (!session.user?.is_admin) return
  try {
    instance.value = await api('GET', '/api/instance')
    publicUrl.value = instance.value!.public_base_url
  } catch {
    /* the section stays hidden */
  }
})
</script>

<template>
  <Modal title="Preferences" wide @close="ui.closeDialog()">
    <div class="tabs">
      <button v-for="(_, name) in TABS" :key="name" class="btn" :class="{ active: tab === name }" @click="(tab = name as string), (message = '')">{{ name }}</button>
    </div>
    <div v-for="field in TABS[tab]" :key="field.key" class="form-row">
      <label :id="`pref-label-${field.key}`" :for="field.type === 'tint' ? undefined : `pref-${field.key}`">{{ field.label }}<span v-if="field.hint" class="dim" style="display: block; font-size: 11px">{{ field.hint }}</span></label>
      <div v-if="field.type === 'tint'" class="tint-picker" role="radiogroup" :aria-labelledby="`pref-label-${field.key}`" @mouseleave="settings.applyTheme()">
        <button
          v-for="[value, label] in field.options" :key="value" type="button" role="radio" class="tint-swatch"
          :class="{ selected: isChosen(value as ThemeChoice) }" :data-swatch="value" :title="label" :aria-label="label"
          :aria-checked="isChosen(value as ThemeChoice)" @mouseenter="previewTheme(value as ThemeChoice)" @click="chooseTheme(value as ThemeChoice)"
        ></button>
      </div>
      <input v-else-if="field.type === 'bool'" :id="`pref-${field.key}`" type="checkbox" :checked="!!settings.prefs[field.key]" @change="update(field, $event)" />
      <select v-else-if="field.type === 'select'" :id="`pref-${field.key}`" class="input" :value="settings.prefs[field.key]" @change="update(field, $event)">
        <option v-for="[value, label] in field.options" :key="value" :value="value">{{ label }}</option>
      </select>
      <input
        v-else :id="`pref-${field.key}`" class="input" :type="field.type === 'number' ? 'number' : 'text'" :min="field.min" :max="field.max"
        :value="settings.prefs[field.key]" @change="update(field, $event)"
      />
    </div>

    <template v-if="tab === 'Reading'">
      <h3 class="section">Per-feed settings</h3>
      <div style="display: flex; flex-wrap: wrap; gap: 6px">
        <button class="btn outline" @click="reset('layout', 'The layout')">Reset layout for all feeds</button>
        <button class="btn outline" @click="reset('view', 'The view')">Reset view for all feeds</button>
        <button class="btn outline" @click="reset('order', 'The order and read filter')">Reset order and read filter</button>
        <button class="btn outline" @click="reset('read_filter', 'The read filter')">Reset read filter</button>
        <button class="btn outline" @click="reset('grid_columns', 'Stories per row')">Reset stories per row</button>
        <button class="btn outline" @click="reset('story_display', 'The story display')">Reset story display</button>
        <button class="btn outline" @click="reset('show_content_preview', 'The content preview')">Reset content preview</button>
        <button class="btn outline" @click="reset('image_preview', 'The image preview')">Reset image preview</button>
        <button class="btn outline" @click="reset('headline_indent', 'The headline indent')">Reset headline indent</button>
        <button class="btn outline" @click="reset('summary_width', 'The summary width')">Reset summary width</button>
      </div>
    </template>

    <template v-if="tab === 'Sharing'">
      <p class="dim">Services shown in the Share menu of a story.</p>
      <div class="share-options">
        <label v-for="service in SHARE_SERVICES" :key="service.key">
          <input
            type="checkbox" :checked="settings.prefs.share_services.includes(service.key)"
            @change="toggleShare(service.key, ($event.target as HTMLInputElement).checked)"
          />
          {{ service.label }}
        </label>
      </div>
    </template>

    <template v-if="tab === 'General' && instance">
      <h3 class="section">Address used by push hubs</h3>
      <p class="dim">
        WebSub hubs call NewsSharp back at this address. It follows the address of the most recent login unless you pin it here.
        {{ instance.manual ? 'Currently pinned.' : 'Currently automatic.' }}
      </p>
      <div style="display: flex; gap: 6px">
        <input v-model="publicUrl" class="input" style="flex: 1" placeholder="https://reader.example" />
        <button class="btn primary" @click="savePublicUrl(publicUrl.trim() || null)">Pin</button>
        <button class="btn outline" :disabled="!instance.manual" @click="savePublicUrl(null)">Automatic</button>
      </div>
    </template>
    <div v-if="message" class="notice" style="margin-top: 12px">{{ message }}</div>
  </Modal>
</template>
