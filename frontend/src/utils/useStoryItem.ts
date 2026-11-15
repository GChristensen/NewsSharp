// Shared computed values of a story row or card (frontend/src/utils/useStoryItem.ts): feed color
// strip (R8.7), date, title and preview with search highlighting (R9.6, R9.7, R12.7).
import { computed } from 'vue'
import { useSettings } from '../stores/settings'
import { useStories } from '../stores/stories'
import { useTree } from '../stores/tree'
import type { Story } from '../types'
import { feedColors } from './colors'
import { displayTitle, highlight, previewText, shortDate } from './format'

export function useStoryItem(story: () => Story, card: boolean) {
  const settings = useSettings()
  const stories = useStories()
  const tree = useTree()
  const feed = computed(() => tree.feedById.get(story().feed_id))
  const colors = computed(() => feedColors(feed.value?.favicon_color, settings.dark))
  return {
    feed,
    feedName: computed(() => feed.value?.title ?? story().feed_title ?? ''),
    stripStyle: computed(() =>
      colors.value ? { background: `linear-gradient(to right, ${colors.value.base} 50%, ${colors.value.fade} 50%)` } : {},
    ),
    cardStripStyle: computed(() => (colors.value ? { background: colors.value.base } : {})),
    // The unread dot wears the feed's icon color; without one it stays neutral like the default icon.
    dotStyle: computed(() => (colors.value && !story().read ? { background: colors.value.base } : {})),
    date: computed(() => shortDate(stories.isSaved ? story().saved_at ?? story().story_date : story().story_date, settings.prefs.timezone, settings.hour12)),
    titleHtml: computed(() => highlight(displayTitle(story().title, story().preview, story().permalink), stories.terms)),
    previewHtml: computed(() => {
      const text = previewText(story().preview, story().title, settings.effective(stories.scope, 'show_content_preview'), card)
      return text ? highlight(text, stories.terms) : ''
    }),
    dimmed: computed(() => story().read && !stories.isSaved),
  }
}
