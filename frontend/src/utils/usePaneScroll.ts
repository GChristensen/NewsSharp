// Scroll behavior of a story pane (frontend/src/utils/usePaneScroll.ts): loading the next page
// near the end or when the pane is not filled and marking stories read on scroll.
import { nextTick, watch, type Ref } from 'vue'
import { useStories } from '../stores/stories'

export function usePaneScroll(pane: Ref<HTMLElement | null>, markOnScroll: () => boolean) {
  const stories = useStories()
  let scrolled = false
  let frame = 0

  function check() {
    frame = 0
    const el = pane.value
    if (!el || !stories.scope) return
    const nearEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 2 * el.clientHeight
    if (nearEnd && !stories.ended && !stories.loading && !stories.error) stories.loadMore()
    if (!scrolled || el.scrollTop <= 0 || stories.isSaved || !markOnScroll()) return
    const box = el.getBoundingClientRect()
    const atEnd = stories.ended && el.scrollTop + el.clientHeight >= el.scrollHeight - 4
    el.querySelectorAll<HTMLElement>('[data-unread]').forEach((node) => {
      const rect = node.getBoundingClientRect()
      // A story scrolled completely above the top edge; at the end of the list, everything visible.
      if (rect.bottom < box.top || (atEnd && rect.top < box.bottom)) {
        const story = stories.find(node.dataset.key!)
        if (story) stories.markRead(story)
      }
    })
  }

  function onScroll() {
    scrolled = true
    if (!frame) frame = requestAnimationFrame(check)
  }

  watch(() => [stories.stories.length, stories.loading], () => nextTick(check))
  watch(
    () => stories.generation,
    () => {
      scrolled = false
      if (pane.value) pane.value.scrollTop = 0
    },
  )
  return { onScroll, check }
}
