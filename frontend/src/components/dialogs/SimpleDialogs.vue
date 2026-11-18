<!-- The small dialogs (frontend/src/components/dialogs/SimpleDialogs.vue): confirmation,
     change password, import / export, keyboard shortcuts. -->
<script setup lang="ts">
import { ref } from 'vue'
import { api, errorMessage } from '../../api'
import { useTree } from '../../stores/tree'
import { useUi } from '../../stores/ui'
import { keyLabel, SHORTCUTS } from '../../utils/shortcuts'
import Modal from '../Modal.vue'

const props = defineProps<{ name: string; options: Record<string, any> }>()
const ui = useUi()
const tree = useTree()
const busy = ref(false)
const error = ref('')

// confirm
function confirm() {
  ui.closeDialog()
  props.options.onConfirm?.()
}

// password
const oldPassword = ref('')
const newPassword = ref('')
async function changePassword() {
  await run(async () => {
    await api('POST', '/api/password', { old_password: oldPassword.value, new_password: newPassword.value })
    ui.closeDialog()
    ui.toast('The password was changed. Other sessions were logged out.')
  })
}

// OPML
const fileInput = ref<HTMLInputElement | null>(null)
const importResult = ref<{ feeds_added: number; feeds_existing: number; folders_added: number; skipped: { title: string; url: string; reason: string }[] } | null>(null)
async function importOpml() {
  const file = fileInput.value?.files?.[0]
  if (!file) return
  await run(async () => {
    const form = new FormData()
    form.append('file', file)
    importResult.value = await api('POST', '/api/opml', undefined, { form, timeout: 60000 })
    await tree.reload()
  })
}

async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Modal v-if="name === 'confirm'" :title="options.title || 'Confirm'" @close="ui.closeDialog()">
    <p>{{ options.message }}</p>
    <template #footer>
      <button class="btn outline" @click="ui.closeDialog()">Cancel</button>
      <button class="btn primary" autofocus @click="confirm">{{ options.confirmLabel || 'OK' }}</button>
    </template>
  </Modal>

  <Modal v-else-if="name === 'password'" title="Change password" @close="ui.closeDialog()">
    <form id="password-form" @submit.prevent="changePassword">
      <div class="form-col">
        <label for="pw-old">Current password</label>
        <input id="pw-old" v-model="oldPassword" class="input" type="password" autocomplete="current-password" required />
      </div>
      <div class="form-col">
        <label for="pw-new">New password (at least 6 characters)</label>
        <input id="pw-new" v-model="newPassword" class="input" type="password" autocomplete="new-password" minlength="6" required />
      </div>
      <div v-if="error" class="error-text">{{ error }}</div>
    </form>
    <template #footer>
      <button class="btn outline" type="button" @click="ui.closeDialog()">Cancel</button>
      <button class="btn primary" type="submit" form="password-form" :disabled="busy">Change password</button>
    </template>
  </Modal>

  <Modal v-else-if="name === 'opml'" title="Import / Export" @close="ui.closeDialog()">
    <h3 class="section" style="margin-top: 0">Import OPML</h3>
    <p class="dim">Feeds and folders of the file are added to your list. Nothing is removed.</p>
    <div style="display: flex; gap: 8px; align-items: center">
      <input ref="fileInput" type="file" accept=".opml,.xml,text/xml,text/x-opml" />
      <button class="btn primary" :disabled="busy" @click="importOpml">Import</button>
      <span v-if="busy" class="spinner" />
    </div>
    <div v-if="error" class="error-text" style="margin-top: 8px">{{ error }}</div>
    <div v-if="importResult" class="notice" style="margin-top: 10px">
      {{ importResult.feeds_added }} feeds added, {{ importResult.feeds_existing }} already subscribed,
      {{ importResult.folders_added }} folders added<span v-if="importResult.skipped.length">, {{ importResult.skipped.length }} skipped:</span>
      <ul v-if="importResult.skipped.length">
        <li v-for="(entry, index) in importResult.skipped" :key="index">{{ entry.title || entry.url }} — {{ entry.reason }}</li>
      </ul>
    </div>
    <h3 class="section">Export OPML</h3>
    <a class="btn outline" href="/api/opml" download>Download your feeds</a>
  </Modal>

  <Modal v-else-if="name === 'shortcuts'" title="Keyboard shortcuts" wide @close="ui.closeDialog()">
    <div class="shortcuts">
      <div v-for="shortcut in SHORTCUTS" :key="shortcut.label">
        <span>{{ shortcut.label }}</span>
        <span style="white-space: nowrap">
          <template v-for="(key, index) in shortcut.keys" :key="key"><span v-if="index" class="dim"> / </span><kbd>{{ keyLabel(key) }}</kbd></template>
        </span>
      </div>
    </div>
  </Modal>
</template>
