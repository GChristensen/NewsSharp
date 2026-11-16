<!-- Feed statistics dialog (frontend/src/components/dialogs/StatisticsDialog.vue), R18.6. Opening it
     reads stored values only. -->
<script setup lang="ts">
import { Chart, registerables } from 'chart.js'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { refreshFeed } from '../../actions'
import { api, errorMessage } from '../../api'
import { useSettings } from '../../stores/settings'
import { useUi } from '../../stores/ui'
import type { Statistics } from '../../types'
import { bytes, dateTime, relativeTime } from '../../utils/format'
import FeedIcon from '../FeedIcon.vue'
import FetchHistory from '../FetchHistory.vue'
import Modal from '../Modal.vue'

Chart.register(...registerables)

const props = defineProps<{ feedId: number }>()
const settings = useSettings()
const ui = useUi()
const stats = ref<Statistics | null>(null)
const error = ref('')
const monthCanvas = ref<HTMLCanvasElement | null>(null)
const weekdayCanvas = ref<HTMLCanvasElement | null>(null)
let charts: Chart[] = []

const maxHour = computed(() => Math.max(1, ...(stats.value?.hours ?? [1])))
function when(seconds: number): string {
  return dateTime(seconds, settings.prefs.timezone, settings.hour12)
}
function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function draw() {
  if (!stats.value) return
  const accent = token('--accent')
  const dim = token('--text-dim')
  const border = token('--border')
  const scale = { ticks: { color: dim }, grid: { color: border } }
  if (monthCanvas.value) {
    charts.push(
      new Chart(monthCanvas.value, {
        type: 'line',
        data: {
          labels: stats.value.months.map(([key]) => key),
          datasets: [{ data: stats.value.months.map(([, n]) => n), borderColor: accent, backgroundColor: accent, tension: 0.25, pointRadius: 2 }],
        },
        options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: scale, y: { ...scale, beginAtZero: true } } },
      }),
    )
  }
  if (weekdayCanvas.value) {
    charts.push(
      new Chart(weekdayCanvas.value, {
        type: 'radar',
        data: {
          labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
          datasets: [{ data: stats.value.weekdays, borderColor: accent, backgroundColor: accent + '33', pointRadius: 2 }],
        },
        options: {
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { r: { beginAtZero: true, ticks: { display: false }, grid: { color: border }, angleLines: { color: border }, pointLabels: { color: dim } } },
        },
      }),
    )
  }
}

onMounted(async () => {
  try {
    stats.value = await api<Statistics>('GET', `/api/feeds/${props.feedId}/statistics`)
    await nextTick()
    draw()
  } catch (e) {
    error.value = errorMessage(e)
  }
})
onBeforeUnmount(() => charts.forEach((chart) => chart.destroy()))
</script>

<template>
  <Modal title="Statistics" wide @close="ui.closeDialog()">
    <div v-if="error" class="error-text">{{ error }}</div>
    <div v-else-if="!stats"><span class="spinner" style="display: inline-block" /></div>
    <template v-else>
      <div style="display: flex; gap: 10px; align-items: center">
        <FeedIcon :feed-id="feedId" />
        <strong style="font-size: 15px">{{ stats.feed.title }}</strong>
      </div>
      <div class="dim" style="margin-top: 4px; overflow-wrap: anywhere">
        {{ stats.feed.feed_address }}
        <template v-if="stats.feed.feed_link"> · <a :href="stats.feed.feed_link" target="_blank" rel="noopener noreferrer">site</a></template>
      </div>
      <div class="stat-grid">
        <div><span>Last update:</span> {{ relativeTime(stats.last_update) }}</div>
        <div>
          <span>Next update:</span> {{ relativeTime(stats.next_update) }} (every {{ stats.interval_minutes }} minutes<template v-if="stats.reason">; {{ stats.reason }}</template>)
        </div>
        <div><span>Stories last month:</span> {{ stats.stories_last_month }}</div>
        <div><span>Average per month:</span> {{ stats.average_stories_per_month }}</div>
        <div><span>Stored stories:</span> {{ stats.story_count }} ({{ bytes(stats.content_bytes) }})</div>
        <div><span>Last fetch took:</span> {{ stats.last_load_ms }} ms</div>
        <div v-if="stats.push" style="grid-column: 1 / -1">
          <span>Push:</span> {{ stats.push.hub }} — {{ stats.push.verified ? 'verified' : 'not verified yet' }}<template v-if="stats.push.lease_expires">,
          lease until {{ when(stats.push.lease_expires) }}</template><template v-if="stats.push.last_push_at">, last push {{ relativeTime(stats.push.last_push_at) }}</template>
        </div>
      </div>

      <h3 class="section">Stories per month</h3>
      <div class="chart-box"><canvas ref="monthCanvas" /></div>
      <div style="display: grid; grid-template-columns: 240px 1fr; gap: 16px; align-items: start">
        <div>
          <h3 class="section">Weekdays (UTC)</h3>
          <div class="chart-box" style="height: 200px"><canvas ref="weekdayCanvas" /></div>
        </div>
        <div>
          <h3 class="section">Hours of the day</h3>
          <div class="hours">
            <div
              v-for="(count, hour) in stats.hours" :key="hour" :title="`${hour}:00 — ${count} stories`"
              :style="{ background: `color-mix(in srgb, var(--accent) ${Math.round((count / maxHour) * 100)}%, var(--bg-hover))` }"
            >{{ hour }}</div>
          </div>
        </div>
      </div>

      <h3 class="section">Fetch history</h3>
      <FetchHistory :entries="stats.fetch_history" />
      <template v-if="stats.push_history.length">
        <h3 class="section">Push history</h3>
        <table class="data">
          <thead><tr><th>Time</th><th>Message</th></tr></thead>
          <tbody>
            <tr v-for="(entry, index) in stats.push_history" :key="index"><td>{{ when(entry.at) }}</td><td>{{ entry.message }}</td></tr>
          </tbody>
        </table>
      </template>
    </template>
    <template #footer>
      <button class="btn outline" @click="ui.openDialog('feed-settings', { feedId })">Settings</button>
      <button class="btn outline" @click="refreshFeed(feedId)">Refresh now</button>
      <button class="btn primary" @click="ui.closeDialog()">Close</button>
    </template>
  </Modal>
</template>
