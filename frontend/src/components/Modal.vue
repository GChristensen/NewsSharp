<!-- Modal dialog frame (frontend/src/components/Modal.vue): modal, closes with Esc, traps focus. -->
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'

defineProps<{ title: string; wide?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const box = ref<HTMLElement | null>(null)
let previous: Element | null = null

function focusable(): HTMLElement[] {
  if (!box.value) return []
  return Array.from(box.value.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter(
    (el) => !el.hasAttribute('disabled') && el.offsetParent !== null,
  )
}

function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    emit('close')
  } else if (event.key === 'Tab') {
    const items = focusable()
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}

onMounted(() => {
  previous = document.activeElement
  const target = box.value?.querySelector<HTMLElement>('[autofocus], input, select') ?? focusable()[0]
  target?.focus()
})
onBeforeUnmount(() => (previous as HTMLElement | null)?.focus?.())
</script>

<template>
  <div class="modal-backdrop" @mousedown.self="emit('close')" @keydown="onKey">
    <div ref="box" class="modal" :class="{ wide }" role="dialog" aria-modal="true" :aria-label="title">
      <div class="modal-head">
        <span>{{ title }}</span>
        <span class="spacer" />
        <button class="btn icon-only" title="Close" @click="emit('close')"><Icon name="x" /></button>
      </div>
      <div class="modal-body"><slot /></div>
      <div v-if="$slots.footer" class="modal-foot"><slot name="footer" /></div>
    </div>
  </div>
</template>
