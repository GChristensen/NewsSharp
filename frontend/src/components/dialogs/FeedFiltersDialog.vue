<!-- Feed filters dialog (frontend/src/components/dialogs/FeedFiltersDialog.vue): the Promote, Demote and
     Hide lists of one feed. The user's global filters are appended to them. The filters come from the
     tree, so changes made in other tabs show up here as well. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, errorMessage } from '../../api'
import { useStories } from '../../stores/stories'
import { useTree } from '../../stores/tree'
import { useUi } from '../../stores/ui'
import type { FeedCounts, FilterKind } from '../../types'
import FeedIcon from '../FeedIcon.vue'
import FilterLists from '../FilterLists.vue'
import Icon from '../Icon.vue'
import Modal from '../Modal.vue'

const props = defineProps<{ feedId: number }>()
const tree = useTree()
const ui = useUi()
const feed = computed(() => tree.feedById.get(props.feedId))
const error = ref('')

/** Save one of the feed's filter lists; the counts and the open list follow at once. */
async function updateFilter(kind: FilterKind, lines: string[]) {
  if (!feed.value) return
  const filters = { ...feed.value.filters, [kind]: lines }
  error.value = ''
  try {
    const result = await api<{ counts: Record<string, FeedCounts> }>('PATCH', `/api/subscriptions/${props.feedId}`, { filters })
    tree.updateFeed(props.feedId, { filters })
    tree.applyCounts(result.counts)
    const stories = useStories()
    if (stories.scope && stories.inScope(props.feedId)) stories.reload()
  } catch (e) {
    error.value = errorMessage(e)
  }
}

// Unsubscribed in another tab.
watch(feed, (current) => {
  if (!current) ui.closeDialog()
})
</script>

<template>
  <Modal title="Feed filters" wide @close="ui.closeDialog()">
    <template v-if="feed">
      <div style="display: flex; gap: 10px; align-items: center">
        <FeedIcon :feed-id="feedId" />
        <strong style="font-size: 15px">{{ feed.title }}</strong>
      </div>
      <p class="dim filter-help">
        Regular expressions for this feed alone, one per line, matched like the global filters against a story's
        title, author and summary. Your global filters are added after these lines.
      </p>
      <FilterLists id-prefix="feed-filter" :values="feed.filters" @update="updateFilter" />
      <div v-if="error" class="error-text" style="margin-top: 4px">{{ error }}</div>
      <div class="filter-switch">
        <button class="btn outline" @click="ui.openDialog('preferences', { tab: 'Filters' })">
          <Icon name="prefs-sliders" />Global filters…
        </button>
        <span class="dim">Applied to every feed.</span>
      </div>
    </template>
    <template #footer>
      <button class="btn outline" @click="ui.openDialog('feed-settings', { feedId })">Settings</button>
      <button class="btn outline" @click="ui.openDialog('statistics', { feedId })">Statistics</button>
      <button class="btn primary" @click="ui.closeDialog()">Close</button>
    </template>
  </Modal>
</template>
