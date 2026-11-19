<!-- Feed settings dialog (frontend/src/components/dialogs/FeedSettingsDialog.vue), after NewsBlur's site
     settings: the feed address, the site it belongs to, fetching through the proxy of
     config.yaml, and the recorded fetch attempts. The feed fields come from the tree, so changes made in
     other tabs show up here as well. -->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { refreshFeed } from '../../actions'
import { api, errorMessage } from '../../api'
import { useTree } from '../../stores/tree'
import { useUi } from '../../stores/ui'
import type { FeedSettings, IconFetchResult } from '../../types'
import FeedIcon from '../FeedIcon.vue'
import FetchHistory from '../FetchHistory.vue'
import Icon from '../Icon.vue'
import Modal from '../Modal.vue'

const props = defineProps<{ feedId: number }>()
const tree = useTree()
const ui = useUi()
const feed = computed(() => tree.feedById.get(props.feedId))
const details = ref<FeedSettings | null>(null)
const loadError = ref('')

const address = ref(feed.value?.feed_address ?? '')
const addressBusy = ref(false)
const addressError = ref('')
const addressChanged = computed(() => !!address.value.trim() && address.value.trim() !== feed.value?.feed_address)

const proxyBusy = ref(false)
const proxyError = ref('')
// A feed set to use the proxy before it was removed from config.yaml can still be switched back.
const proxyDisabled = computed(() => proxyBusy.value || (!details.value?.proxy_available && !feed.value?.use_proxy))

const iconBusy = ref(false)
const iconError = ref('')
const iconResult = ref<IconFetchResult | null>(null)
const iconLine = computed(() => {
  const result = iconResult.value
  if (result?.pending) return 'The worker has not answered yet; the icon appears here if it is found.'
  if (result && !result.found) {
    const tried = `${result.tried} address${result.tried === 1 ? '' : 'es'} tried${details.value?.proxy_available ? ', including through the proxy' : ''}`
    return result.out_of_time ? `The site answered too slowly (${tried}).` : `No icon was found (${tried}).`
  }
  if (details.value?.icon_url) return `From ${details.value.icon_url}${details.value.icon_link ? ' (set by hand)' : ''}`
  return details.value ? 'The feed has no icon yet.' : ''
})

// The icon address set by hand, edited in place of the line above.
const iconEditing = ref(false)
const iconDraft = ref('')
const iconInput = ref<HTMLInputElement | null>(null)

function editIconLink() {
  iconDraft.value = details.value?.icon_link || details.value?.icon_url || ''
  iconError.value = ''
  iconEditing.value = true
  nextTick(() => iconInput.value?.select())
}

async function saveIconLink() {
  iconEditing.value = false
  await fetchIcon(iconDraft.value.trim())
}

/** Search for the icon now; with `url`, first keep it as the feed's icon address ("" looks it up again). */
async function fetchIcon(url?: string) {
  iconBusy.value = true
  iconError.value = ''
  iconResult.value = null
  try {
    const body = url === undefined ? {} : { url }
    const result = await api<IconFetchResult>('POST', `/api/feeds/${props.feedId}/icon/fetch`, body, { timeout: 40000 })
    iconResult.value = result
    // Shown at once here, without waiting for the feed:icon event (absent while the event stream is down).
    if (!result.pending && result.found) {
      tree.updateFeed(props.feedId, { has_icon: true, icon_version: Date.now(), favicon_color: result.favicon_color })
    }
    await load()
  } catch (e) {
    iconError.value = errorMessage(e)
    if (url !== undefined) iconEditing.value = true // keep the rejected address for correcting
  } finally {
    iconBusy.value = false
  }
}

async function load() {
  try {
    details.value = await api<FeedSettings>('GET', `/api/feeds/${props.feedId}/settings`)
    loadError.value = ''
  } catch (e) {
    loadError.value = errorMessage(e)
  }
}

async function changeAddress() {
  addressBusy.value = true
  addressError.value = ''
  try {
    const result = await api<{ feed_id: number; feed_address: string }>(
      'POST', `/api/subscriptions/${props.feedId}/address`, { url: address.value.trim() }, { timeout: 40000 },
    )
    await tree.reload()
    ui.toast('The address was changed; the feed is being fetched.')
    if (result.feed_id !== props.feedId) {
      // The address belongs to another feed: the subscription moved there.
      ui.openDialog('feed-settings', { feedId: result.feed_id })
      return
    }
    address.value = result.feed_address
  } catch (e) {
    addressError.value = errorMessage(e)
  } finally {
    addressBusy.value = false
  }
}

async function setProxy(event: Event) {
  const input = event.target as HTMLInputElement
  proxyBusy.value = true
  proxyError.value = ''
  try {
    const result = await api<{ use_proxy: boolean }>('PATCH', `/api/feeds/${props.feedId}/settings`, { use_proxy: input.checked })
    tree.updateFeed(props.feedId, { use_proxy: result.use_proxy })
  } catch (e) {
    proxyError.value = errorMessage(e)
    input.checked = !!feed.value?.use_proxy
  } finally {
    proxyBusy.value = false
  }
}

// The address shown follows changes from elsewhere unless the user is editing it.
watch(
  () => feed.value?.feed_address,
  (current, previous) => {
    if (current && address.value === previous) address.value = current
  },
)
watch(() => tree.fetchedAt[props.feedId], load)
// An icon found later (a search that outlasted the wait, another tab, the scheduler) replaces any
// earlier outcome with its source.
watch(
  () => feed.value?.icon_version,
  async () => {
    await load()
    if (!iconBusy.value && iconResult.value && (iconResult.value.pending || !iconResult.value.found)) iconResult.value = null
  },
)
watch(feed, (current) => {
  // Unsubscribed in another tab; not after an address change that moved the subscription to another feed.
  if (!current && ui.dialog?.props.feedId === props.feedId) ui.closeDialog()
})
onMounted(load)
</script>

<template>
  <Modal title="Feed settings" wide @close="ui.closeDialog()">
    <template v-if="feed">
      <div style="display: flex; gap: 10px; align-items: center">
        <FeedIcon :feed-id="feedId" />
        <strong style="font-size: 15px">{{ feed.title }}</strong>
      </div>
      <div v-if="feed.has_exception" class="notice" style="margin: 10px 0 0">
        <span class="error-text">The last fetch failed: error {{ feed.exception_code }}<template v-if="feed.exception_message">, {{ feed.exception_message }}</template>.</span>
      </div>

      <h3 class="section">Feed address</h3>
      <form @submit.prevent="changeAddress">
        <div style="display: flex; gap: 6px; align-items: center">
          <input id="feed-address" v-model="address" class="input" style="flex: 1" placeholder="https://example.com/feed.xml" aria-label="Feed address" required />
          <span v-if="addressBusy" class="spinner" />
          <button class="btn primary" type="submit" :disabled="addressBusy || !addressChanged">Change</button>
        </div>
        <div class="dim" style="margin-top: 4px">A website address works too: its feed is looked up. Stories already stored are kept.</div>
        <div v-if="addressError" class="error-text" style="margin-top: 4px">{{ addressError }}</div>
      </form>

      <h3 class="section">Website</h3>
      <div style="overflow-wrap: anywhere">
        <a v-if="feed.feed_link" :href="feed.feed_link" target="_blank" rel="noopener noreferrer">{{ feed.feed_link }}</a>
        <span v-else class="dim">The feed does not name its website.</span>
      </div>
      <form v-if="iconEditing" style="margin-top: 8px" @submit.prevent="saveIconLink">
        <div style="display: flex; gap: 6px; align-items: center">
          <FeedIcon :feed-id="feedId" />
          <input
            ref="iconInput" v-model="iconDraft" class="input" style="flex: 1; min-width: 0"
            placeholder="https://example.com/icon.png" aria-label="Icon address" @keydown.esc.stop="iconEditing = false"
          />
          <button class="btn primary" type="submit">Save</button>
          <button class="btn outline" type="button" @click="iconEditing = false">Cancel</button>
        </div>
        <div class="dim" style="margin-top: 4px">The icon is always fetched from this address. Leave it empty to look the icon up on the site again.</div>
      </form>
      <div v-else style="display: flex; gap: 8px; align-items: center; margin-top: 8px">
        <FeedIcon :feed-id="feedId" />
        <span class="dim" style="flex: 1; min-width: 0; overflow-wrap: anywhere">{{ iconLine }}</span>
        <span v-if="iconBusy" class="spinner" />
        <button class="btn icon-only" type="button" title="Set the icon address" aria-label="Set the icon address" :disabled="iconBusy" @click="editIconLink">
          <Icon name="edit" />
        </button>
        <button class="btn outline" type="button" :disabled="iconBusy" @click="fetchIcon()">Fetch icon</button>
      </div>
      <div v-if="iconError" class="error-text" style="margin-top: 4px">{{ iconError }}</div>

      <h3 class="section">Fetching</h3>
      <label style="display: flex; gap: 8px; align-items: center">
        <input type="checkbox" :checked="feed.use_proxy" :disabled="proxyDisabled" @change="setProxy" />
        Fetch through the proxy
      </label>
      <div v-if="details" class="dim" style="margin-top: 4px">
        <template v-if="details.proxy_available">
          The feed, its icon and the text of its stories are downloaded through the proxy set in config.yaml.
          This applies to everyone subscribed to the feed.
        </template>
        <template v-else>No proxy is configured (<code>proxy.url</code> in config.yaml), so the feed is fetched directly.</template>
      </div>
      <div v-if="proxyError" class="error-text" style="margin-top: 4px">{{ proxyError }}</div>

      <h3 class="section">Fetch history</h3>
      <div v-if="loadError" class="error-text">{{ loadError }}</div>
      <div v-else-if="!details"><span class="spinner" style="display: inline-block" /></div>
      <FetchHistory v-else :entries="details.fetch_history" />
    </template>
    <template #footer>
      <button class="btn accent-outline" @click="ui.openDialog('feed-filters', { feedId })">Filters</button>
      <button class="btn outline" @click="ui.openDialog('statistics', { feedId })">Statistics</button>
      <button class="btn outline" @click="refreshFeed(feedId)">Refresh now</button>
      <button class="btn primary" @click="ui.closeDialog()">Close</button>
    </template>
  </Modal>
</template>
