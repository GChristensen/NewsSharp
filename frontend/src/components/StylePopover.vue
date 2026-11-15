<!-- The style popovers (frontend/src/components/StylePopover.vue): the display options of item 10's
     toggle inventory as one-click segmented rows, like NewsBlur's style popover. The story list part
     opens from the top bar, the story part from the bottom bar. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { effectiveLayout, setAnchor } from '../actions'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import type { Prefs, ViewKey } from '../types'
import Icon from './Icon.vue'

interface Choice {
  value: string
  title: string
  label?: string
  icon?: string
  font?: string
}
interface Row {
  title: string
  icon: string
  choices: Choice[]
  current: string
  pick(value: string): void
  vertical?: boolean
}
interface Section {
  title: string
  rows: Row[]
}

const props = defineProps<{ part: 'list' | 'story' }>()
const emit = defineEmits<{ close: [] }>()
const settings = useSettings()
const stories = useStories()
const root = ref<HTMLElement | null>(null)

/** A press outside the popover closes it; its toggle button toggles it itself. */
function pressOutside(event: PointerEvent) {
  const target = event.target as HTMLElement | null
  if (!target || root.value?.contains(target) || target.closest('[data-style-toggle]')) return
  emit('close')
}
onMounted(() => window.addEventListener('pointerdown', pressOutside, true))
onBeforeUnmount(() => window.removeEventListener('pointerdown', pressOutside, true))

const SIZE_NAMES: Record<string, string> = { xs: 'Extra small', s: 'Small', m: 'Medium', l: 'Large', xl: 'Extra large' }
const sizes = (labels: string[]): Choice[] =>
  Object.keys(SIZE_NAMES).map((value, index) => ({ value, label: labels[index], title: SIZE_NAMES[value] }))
const FONTS: [string, string][] = [
  ['sans-serif', 'Sans-serif'], ['serif', 'Serif'], ['system', 'System'], ['georgia', 'Georgia'], ['palatino', 'Palatino'],
  ['helvetica', 'Helvetica'], ['segoe', 'Segoe UI'], ['verdana', 'Verdana'], ['monospace', 'Monospace'],
]

/** A row bound to one preference. */
function prefRow<K extends keyof Prefs>(key: K, title: string, icon: string, choices: Choice[], vertical = false): Row {
  return {
    title, icon, choices, vertical,
    current: String(settings.prefs[key]),
    pick: (value) => settings.set(key, value as Prefs[K]),
  }
}

/** A row bound to a view setting of the open scope (R19.3); the preference is the default. */
function viewRow(key: ViewKey, title: string, icon: string, choices: Choice[]): Row {
  return {
    title, icon, choices,
    current: settings.effective(stories.scope, key),
    pick: (value) => stories.scope && settings.setView(stories.scope, key, value),
  }
}

const listSections = computed<Section[]>(() => {
  const layout = effectiveLayout()
  const sections: Section[] = [
    {
      title: 'Story titles',
      rows: [
        prefRow('density', 'Density', 'list', [
          { value: 'compact', label: 'Compact', title: 'Compact' },
          { value: 'comfortable', label: 'Comfortable', title: 'Comfortable' },
        ]),
        viewRow('show_content_preview', 'Content preview (this site)', 'preview-m', [
          { value: 'title', label: 'Title only', title: 'Title only' },
          { value: 'small', icon: 'preview-s', title: 'Small preview' },
          { value: 'medium', icon: 'preview-m', title: 'Medium preview' },
          { value: 'large', icon: 'preview-l', title: 'Large preview' },
        ]),
        viewRow('image_preview', 'Image preview (this site)', 'image', [
          { value: 'none', label: 'No image', title: 'No image' },
          { value: 'small-left', icon: 'image-small-left', title: 'Small, left' },
          { value: 'large-left', icon: 'image-large-left', title: 'Large, left' },
          { value: 'large-right', icon: 'image-large-right', title: 'Large, right' },
          { value: 'small-right', icon: 'image-small-right', title: 'Small, right' },
        ]),
        prefRow('feed_size', 'Title size', 'text-size', sizes(['XS', 'S', 'M', 'L', 'XL'])),
      ],
    },
  ]
  if (layout === 'split') {
    sections.push({
      title: 'Split',
      rows: [{
        title: 'Story titles pane', icon: 'split',
        choices: [
          { value: 'west', icon: 'pane-west', label: 'Left', title: 'Titles on the left' },
          { value: 'north', icon: 'pane-north', label: 'Top', title: 'Titles on top' },
          { value: 'south', icon: 'pane-south', label: 'Bottom', title: 'Titles at the bottom' },
        ],
        current: settings.prefs.story_pane_anchor,
        pick: (value) => setAnchor(value as Prefs['story_pane_anchor']),
      }],
    })
  }
  if (layout === 'grid') {
    sections.push({
      title: 'Grid',
      rows: [
        viewRow('grid_columns', 'Stories per row (this site)', 'columns',
          ['1', '2', '3', '4', '5', '6'].map((value) => ({ value, label: value, title: `${value} per row` }))),
        prefRow('grid_height', 'Card height', 'height', sizes(['XS', 'S', 'M', 'L', 'XL'])),
      ],
    })
  }
  return sections
})

const storySections = computed<Section[]>(() => {
  const layoutRows: Row[] = [{
    // full_width_story is folded into the Full width choice of the position row.
    title: 'Story position', icon: 'align-center',
    choices: [
      { value: 'left', icon: 'align-left', title: 'Left' },
      { value: 'center', icon: 'align-center', title: 'Center' },
      { value: 'right', icon: 'align-right', title: 'Right' },
      { value: 'stretch', icon: 'align-stretch', label: 'Full width', title: 'Full width' },
    ],
    current: settings.prefs.full_width_story ? 'stretch' : settings.prefs.story_position,
    pick: (value) => {
      settings.set('full_width_story', false)
      settings.set('story_position', value as Prefs['story_position'])
    },
  }]
  if (effectiveLayout() === 'split') {
    layoutRows.push({
      title: 'Feed view in Split', icon: 'stack',
      choices: [
        { value: 'all', icon: 'stack', label: 'All stories', title: 'All loaded stories, one after another' },
        { value: 'single', icon: 'single', label: 'Single story', title: 'Only the selected story' },
      ],
      current: settings.prefs.feed_view_single_story ? 'single' : 'all',
      pick: (value) => settings.set('feed_view_single_story', value === 'single'),
    })
  }
  return [
    { title: 'Story layout', rows: layoutRows },
    {
      title: 'Typography',
      rows: [
        prefRow('story_size', 'Text size', 'text-size', sizes(['XS', 'S', 'M', 'L', 'XL'])),
        prefRow('story_line_spacing', 'Line spacing', 'line-spacing',
          Object.keys(SIZE_NAMES).map((value) => ({ value, icon: `spacing-${value}`, title: `${SIZE_NAMES[value]} spacing` }))),
        prefRow('story_styling', 'Font', 'type', FONTS.map(([value, label]) => ({ value, label, title: label, font: value })), true),
      ],
    },
  ]
})

const sections = computed(() => (props.part === 'list' ? listSections.value : storySections.value))
</script>

<template>
  <div ref="root" class="popover style-popover" @keydown.esc.stop="$emit('close')">
    <section v-for="section in sections" :key="section.title">
      <div class="popover-title">{{ section.title }}</div>
      <div v-for="row in section.rows" :key="row.title" class="seg-row" :title="row.title">
        <Icon :name="row.icon" class="seg-row-icon" />
        <div class="segmented" :class="{ vertical: row.vertical }">
          <button
            v-for="choice in row.choices" :key="choice.value" class="btn" :class="[{ active: row.current === choice.value }, choice.font ? `font-${choice.font}` : '']"
            :title="choice.title" @click="row.pick(choice.value)"
          >
            <Icon v-if="choice.icon" :name="choice.icon" />
            <span v-if="choice.label">{{ choice.label }}</span>
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
