<!-- The context / dropdown menu (frontend/src/components/ContextMenu.vue). -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useUi, type MenuItem } from '../stores/ui'
import Icon from './Icon.vue'

const ui = useUi()
const box = ref<HTMLElement | null>(null)
const position = ref({ left: 0, top: 0 })

watch(
  () => ui.menu,
  async (menu) => {
    if (!menu) return
    position.value = { left: menu.x, top: menu.y }
    await nextTick()
    const rect = box.value?.getBoundingClientRect()
    if (!rect) return
    // Keep the menu inside the window.
    position.value = {
      left: Math.max(4, Math.min(menu.x, window.innerWidth - rect.width - 4)),
      top: menu.y + rect.height > window.innerHeight - 4 ? Math.max(4, menu.y - rect.height - 8) : menu.y,
    }
  },
)

/** Items line up when any item of the menu has an icon or a check mark. */
const hasIcons = computed(() => !!ui.menu?.items.some((item) => item.icon || item.checked !== undefined))

function run(item: MenuItem) {
  ui.closeMenu()
  item.action?.()
}
</script>

<template>
  <div v-if="ui.menu" style="position: fixed; inset: 0; z-index: 199" @mousedown="ui.closeMenu()" @contextmenu.prevent="ui.closeMenu()" />
  <div v-if="ui.menu" ref="box" class="menu" :style="{ left: position.left + 'px', top: position.top + 'px' }">
    <template v-for="(item, index) in ui.menu.items" :key="index">
      <hr v-if="item.separator" />
      <button v-else :class="{ danger: item.danger }" :disabled="item.disabled" @click="run(item)">
        <span v-if="item.checked !== undefined" class="check"><Icon v-if="item.checked" name="check" style="width: 14px; height: 14px" /></span>
        <span v-else-if="hasIcons" class="menu-icon"><Icon v-if="item.icon" :name="item.icon" style="width: 14px; height: 14px" /></span>
        {{ item.label }}
      </button>
    </template>
  </div>
</template>
