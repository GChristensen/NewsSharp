<!-- Toasts at the bottom center (frontend/src/components/Toasts.vue). -->
<script setup lang="ts">
import { useUi, type Toast } from '../stores/ui'

const ui = useUi()
function act(toast: Toast) {
  ui.dismiss(toast.id)
  toast.action?.()
}
</script>

<template>
  <div class="toasts">
    <div v-for="toast in ui.toasts" :key="toast.id" class="toast" :class="toast.kind" role="status">
      <span>{{ toast.text }}</span>
      <button v-if="toast.actionLabel" @click="act(toast)">{{ toast.actionLabel }}</button>
    </div>
  </div>
</template>
