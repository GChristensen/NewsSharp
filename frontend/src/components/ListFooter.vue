<!-- The end of a story list (frontend/src/components/ListFooter.vue): loading, error with retry
     (R17.7), the end marker (R17.4) or the empty state. -->
<script setup lang="ts">
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'

const stories = useStories()
const settings = useSettings()
</script>

<template>
  <div class="list-footer">
    <span v-if="stories.loading" class="spinner" style="display: inline-block" />
    <template v-else-if="stories.error">
      <div class="error-text">{{ stories.error }}</div>
      <button class="btn outline" style="margin-top: 8px" @click="stories.loadMore()">Retry</button>
    </template>
    <template v-else-if="stories.ended && !stories.stories.length">
      <span v-if="stories.query.trim().length >= 2">No stories match the search.</span>
      <span v-else-if="stories.isSaved">Nothing is saved for later here.</span>
      <span v-else-if="settings.effective(stories.scope, 'read_filter') === 'unread'">No unread stories.</span>
      <span v-else>No stories.</span>
    </template>
    <span v-else-if="stories.ended">End of stories</span>
  </div>
</template>
