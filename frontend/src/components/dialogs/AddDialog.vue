<!-- Add site dialog (frontend/src/components/dialogs/AddDialog.vue), R7.12 and R20.10: a URL field,
     a folder picker and a "New folder" field; a /subscribe/<url> link arrives here pre-filled. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { openScope } from '../../actions'
import { api, errorMessage } from '../../api'
import { useTree } from '../../stores/tree'
import { useUi } from '../../stores/ui'
import type { TreePayload } from '../../types'
import Modal from '../Modal.vue'

const props = defineProps<{ url?: string; folderId?: number }>()
const tree = useTree()
const ui = useUi()
const url = ref(props.url ?? '')
const folderId = ref<number | null>(props.folderId ?? null)
const newFolder = ref('')
const busy = ref(false)
const error = ref('')
const found = ref<{ title: string; feed_address: string } | null>(null)

const folderOptions = computed(() => {
  const options: { id: number; label: string }[] = []
  const walk = (parent: number | null, depth: number) => {
    for (const item of tree.children(parent)) {
      if (item.kind !== 'folder') continue
      options.push({ id: item.folder.id, label: '  '.repeat(depth) + item.folder.title })
      walk(item.folder.id, depth + 1)
    }
  }
  walk(null, 0)
  return options
})

/** A pre-filled URL is looked up at once: its title is shown, or the feed is opened if subscribed. */
async function discover() {
  if (!url.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    const result = await api('POST', '/api/feeds/discover', { url: url.value.trim() }, { timeout: 40000 })
    if (result.subscribed_feed_id) {
      ui.closeDialog()
      openScope(`feed:${result.subscribed_feed_id}`)
      return
    }
    found.value = result
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

async function submit() {
  if (!url.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await api<{ feed_id: number; tree: TreePayload }>(
      'POST', '/api/subscriptions',
      { url: url.value.trim(), folder_id: folderId.value, new_folder: newFolder.value.trim() || undefined },
      { timeout: 40000 },
    )
    tree.setTree(result.tree)
    ui.closeDialog()
    openScope(`feed:${result.feed_id}`)
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (props.url) discover()
})
</script>

<template>
  <Modal title="Add a site" @close="ui.closeDialog()">
    <form id="add-form" @submit.prevent="submit">
      <div class="form-col">
        <label for="add-url">Feed or website address</label>
        <input id="add-url" v-model="url" class="input" placeholder="https://example.com" autofocus @input="found = null" />
      </div>
      <div v-if="found" class="notice">Found: <strong>{{ found.title || found.feed_address }}</strong></div>
      <div class="form-col">
        <label for="add-folder">Folder</label>
        <select id="add-folder" v-model="folderId" class="input">
          <option :value="null">Top level</option>
          <option v-for="option in folderOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
        </select>
      </div>
      <div class="form-col">
        <label for="add-new-folder">New folder (optional, created inside the folder above)</label>
        <input id="add-new-folder" v-model="newFolder" class="input" />
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
    </form>
    <template #footer>
      <span v-if="busy" class="spinner" style="align-self: center" />
      <button class="btn outline" type="button" @click="ui.closeDialog()">Cancel</button>
      <button class="btn primary" type="submit" form="add-form" :disabled="busy || !url.trim()">Subscribe</button>
    </template>
  </Modal>
</template>
