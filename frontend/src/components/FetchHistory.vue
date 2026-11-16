<!-- Table of a feed's recorded fetch attempts (frontend/src/components/FetchHistory.vue), shown by the
     statistics (R18.6) and feed settings dialogs. -->
<script setup lang="ts">
import { useSettings } from '../stores/settings'
import type { FetchLogEntry } from '../types'
import { dateTime } from '../utils/format'

defineProps<{ entries: FetchLogEntry[] }>()
const settings = useSettings()
</script>

<template>
  <table class="data">
    <thead><tr><th>Time</th><th>Code</th><th>Message</th><th>Duration</th></tr></thead>
    <tbody>
      <tr v-for="(entry, index) in entries" :key="index">
        <td>{{ dateTime(entry.at, settings.prefs.timezone, settings.hour12) }}</td>
        <td :class="{ 'error-text': ![200, 304].includes(entry.code) }">{{ entry.code }}</td>
        <td>{{ entry.message }}</td>
        <td>{{ entry.duration_ms }} ms</td>
      </tr>
      <tr v-if="!entries.length"><td colspan="4" class="dim">Not fetched yet.</td></tr>
    </tbody>
  </table>
</template>
