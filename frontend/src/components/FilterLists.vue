<!-- The Promote, Demote and Hide pattern lists (frontend/src/components/FilterLists.vue), side by side:
     the global filters in the preferences and the filters of one feed in its settings. A list is checked
     and emitted when its text area loses focus; an invalid line is shown under its list instead. -->
<script setup lang="ts">
import { ref } from 'vue'
import type { FeedFilters, FilterKind } from '../types'

const props = defineProps<{ values: FeedFilters; idPrefix: string }>()
const emit = defineEmits<{ update: [kind: FilterKind, lines: string[]] }>()

const LISTS: [FilterKind, string, string][] = [
  ['promote', 'Promote', 'Listed first'],
  ['demote', 'Demote', 'Listed last'],
  ['hide', 'Hide', 'Left out of lists and unread counts'],
]
const LINES_MAX = 200 // FILTER_LINES_MAX and FILTER_LINE_CHARS in accounts/prefs.py
const LINE_CHARS = 500
const errors = ref<Partial<Record<FilterKind, string>>>({})

/** The first problem of a list of lines, or '' when every line is a usable regular expression. */
function problem(lines: string[]): string {
  if (lines.length > LINES_MAX) return `At most ${LINES_MAX} lines.`
  for (const [index, line] of lines.entries()) {
    if (line.length > LINE_CHARS) return `Line ${index + 1} is longer than ${LINE_CHARS} characters.`
    try {
      new RegExp(line, 'i')
    } catch (exception) {
      return `Line ${index + 1}: ${(exception as Error).message}`
    }
  }
  return ''
}

function change(kind: FilterKind, event: Event) {
  const lines = (event.target as HTMLTextAreaElement).value.split('\n').map((line) => line.trim()).filter(Boolean)
  const error = problem(lines)
  errors.value = { ...errors.value, [kind]: error }
  if (!error && lines.join('\n') !== props.values[kind].join('\n')) emit('update', kind, lines)
}
</script>

<template>
  <div class="filter-columns">
    <div v-for="[kind, label, hint] in LISTS" :key="kind" class="filter-column">
      <label :for="`${idPrefix}-${kind}`">{{ label }}<span class="dim" style="display: block; font-size: 11px">{{ hint }}</span></label>
      <textarea
        :id="`${idPrefix}-${kind}`" class="input filter-input" spellcheck="false" :value="values[kind].join('\n')"
        @change="change(kind, $event)"
      ></textarea>
      <div v-if="errors[kind]" class="filter-error">{{ errors[kind] }}</div>
    </div>
  </div>
</template>
