<!-- A feed's icon or the neutral default (frontend/src/components/FeedIcon.vue), R8.6, R8.9. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useTree } from '../stores/tree'
import Icon from './Icon.vue'

const props = defineProps<{ feedId: number | null }>()
const tree = useTree()
const feed = computed(() => (props.feedId === null ? undefined : tree.feedById.get(props.feedId)))
const failed = ref(false)
watch(() => feed.value?.icon_version, () => (failed.value = false))
</script>

<template>
  <img
    v-if="feed?.has_icon && !failed" class="feed-icon" alt="" draggable="false"
    :src="`/api/feeds/${feed.id}/icon?v=${feed.icon_version}`" @error="failed = true"
  />
  <span v-else class="feed-icon placeholder"><Icon name="rss" style="width: 10px; height: 10px" /></span>
</template>
