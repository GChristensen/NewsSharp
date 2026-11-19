// The open story list (frontend/src/stores/stories.ts): keyset paging,
// request cancellation, optimistic read state with a batched upload queue, saved stories.
import { defineStore } from 'pinia'
import { api, ApiError, errorMessage, isAbort } from '../api'
import type { FeedCounts, MarkReadResult, MarkReadUndoResult, SavedCounts, Story } from '../types'
import { useSettings } from './settings'
import { useTree } from './tree'
import { useUi } from './ui'

const PAGE_SIZE = 30
const READ_FLUSH_MS = 500 // read marks are sent in batches this often

let controller: AbortController | null = null
let readTimer: number | undefined
let selectTimer: number | undefined
let flushTimer: number | undefined
let retryDelay = 1000
let loaded: Promise<void> = Promise.resolve()

interface Page {
  stories: Story[]
  next_cursor: string | null
  terms: string[]
}

export function storyKey(story: { id: number | null; saved_id: number | null }, savedScope: boolean): string {
  return savedScope || story.id === null ? `v${story.saved_id}` : `s${story.id}`
}

export const useStories = defineStore('stories', {
  state: () => ({
    scope: null as string | null,
    stories: [] as Story[],
    cursor: null as string | null,
    ended: false,
    loading: false,
    error: '',
    selected: null as Story | null,
    query: '',
    /** Hidden stories (the Hide filter) are listed, dimmed: `?hidden=1` on a feed's route. */
    showHidden: false,
    terms: [] as string[],
    newCount: 0,
    history: [] as string[],
    generation: 0,
    readQueue: [] as number[],
    pendingRefresh: null as number | null,
  }),
  getters: {
    isSaved(state): boolean {
      return !!state.scope && state.scope.startsWith('saved')
    },
    selectedKey(state): string | null {
      return state.selected?.key ?? null
    },
    selectedIndex(state): number {
      return state.selected ? state.stories.findIndex((s) => s.key === state.selected!.key) : -1
    },
    /** The single feed of the scope, if it is one. */
    feedId(state): number | null {
      const match = state.scope?.match(/^(?:saved:)?feed:(\d+)$/)
      return match ? Number(match[1]) : null
    },
    multiFeed(): boolean {
      return this.feedId === null
    },
  },
  actions: {
    whenLoaded(): Promise<void> {
      return loaded
    },
    order(): string {
      return useSettings().effective(this.scope, 'order')
    },
    /** Open a scope: abort requests of the previous one and load the first page. */
    open(scope: string, options: { storyId?: number | null; query?: string; showHidden?: boolean } = {}): Promise<void> {
      this.generation++
      controller?.abort()
      controller = new AbortController()
      clearTimeout(selectTimer)
      this.scope = scope
      this.stories = []
      this.cursor = null
      this.ended = false
      this.error = ''
      this.selected = null
      this.newCount = 0
      this.terms = []
      if (options.query !== undefined) this.query = options.query
      this.showHidden = !!options.showHidden
      const match = scope.match(/^feed:(\d+)$/)
      useTree().openFeedId = match ? Number(match[1]) : null
      loaded = (async () => {
        await this.fetchPage()
        if (options.storyId) await this.selectById(options.storyId)
      })()
      return loaded
    },
    close() {
      this.generation++
      controller?.abort()
      this.scope = null
      this.stories = []
      this.selected = null
      this.newCount = 0
    },
    async fetchPage() {
      if (!this.scope || this.loading || this.ended) return
      const generation = this.generation
      const settings = useSettings()
      const params = new URLSearchParams({
        scope: this.scope,
        filter: settings.effective(this.scope, 'read_filter'),
        order: this.order(),
        limit: String(PAGE_SIZE),
      })
      if (this.query.trim().length >= 2) params.set('q', this.query.trim())
      if (this.showHidden) params.set('hidden', '1')
      if (this.cursor) params.set('cursor', this.cursor)
      this.loading = true
      this.error = ''
      try {
        const page = await api<Page>('GET', `/api/stories?${params}`, undefined, { signal: controller?.signal })
        if (generation !== this.generation) return // the scope is no longer open
        const known = new Set(this.stories.map((s) => s.key))
        for (const story of page.stories) {
          story.key = storyKey(story, this.isSaved)
          if (!known.has(story.key)) this.stories.push(story)
        }
        this.cursor = page.next_cursor
        this.ended = page.next_cursor === null
        this.terms = page.terms
      } catch (error) {
        if (generation !== this.generation || isAbort(error)) return
        this.error = errorMessage(error)
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    loadMore(): Promise<void> {
      return this.fetchPage()
    },
    /** Reload from the start, keeping the selected story selected. */
    async reload() {
      if (!this.scope) return
      const selected = this.selected
      this.generation++
      controller?.abort()
      controller = new AbortController()
      this.cursor = null
      this.ended = false
      this.loading = false
      this.newCount = 0
      const previous = this.stories
      this.stories = []
      loaded = this.fetchPage()
      await loaded
      if (this.error) this.stories = previous
      if (selected) {
        const again = this.stories.find((s) => s.key === selected.key)
        this.selected = again ?? selected
      }
    },
    setShowHidden(showHidden: boolean) {
      if (showHidden === this.showHidden) return
      this.showHidden = showHidden
      if (this.scope) this.reload()
    },
    setQuery(query: string) {
      if (query === this.query) return
      this.query = query
      if (this.scope) this.reload()
    },
    find(key: string): Story | undefined {
      return this.stories.find((s) => s.key === key) ?? (this.selected?.key === key ? this.selected : undefined)
    },
    /** A deep link to a story that is not in the first page loads the story directly. */
    async selectById(storyId: number) {
      let story = this.stories.find((s) => s.id === storyId)
      if (!story) {
        try {
          story = await api<Story>('GET', `/api/stories/${storyId}`)
          story.key = storyKey(story, this.isSaved)
        } catch {
          return
        }
      }
      this.select(story)
    },
    /** Select a story and mark it read after read_story_delay. */
    select(story: Story | null) {
      clearTimeout(selectTimer)
      if (!story) {
        this.selected = null
        return
      }
      if (this.selected?.key === story.key) return
      this.selected = story
      this.history.push(story.key)
      if (this.history.length > 200) this.history.shift()
      const delay = useSettings().prefs.read_story_delay
      if (delay === 0) this.markRead(story)
      else if (delay > 0) selectTimer = window.setTimeout(() => this.markRead(story), delay * 1000)
    },
    previousInHistory() {
      this.history.pop()
      const key = this.history.pop()
      const story = key ? this.find(key) : undefined
      if (story) this.select(story)
    },

    // ---------------------------------------------------------- read state

    markRead(story: Story) {
      if (story.read || story.id === null || this.isSaved) return
      story.read = true
      useTree().adjustUnread(story.feed_id, -1, story.hidden)
      this.readQueue.push(story.id)
      if (flushTimer === undefined) flushTimer = window.setTimeout(() => this.flushRead(), READ_FLUSH_MS)
    },
    /** Send queued read ids in one request; a failed request keeps them queued and retries. */
    async flushRead() {
      clearTimeout(flushTimer)
      flushTimer = undefined
      if (!this.readQueue.length) return
      const ids = this.readQueue.splice(0)
      try {
        const result = await api<{ counts: Record<string, FeedCounts> }>('POST', '/api/read', { story_ids: ids })
        retryDelay = 1000
        if (!this.readQueue.length) useTree().applyCounts(result.counts)
      } catch (error) {
        if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 403) return
        this.readQueue.unshift(...ids)
        clearTimeout(readTimer)
        readTimer = window.setTimeout(() => this.flushRead(), retryDelay)
        retryDelay = Math.min(retryDelay * 2, 30000)
      }
    },
    async markUnread(story: Story) {
      if (story.id === null || !story.read) return
      try {
        const result = await api<{ counts: Record<string, FeedCounts> }>('POST', '/api/unread', { story_id: story.id })
        story.read = false
        useTree().applyCounts(result.counts)
      } catch (error) {
        useUi().error(errorMessage(error))
      }
    },
    toggleRead(story: Story) {
      if (story.read) this.markUnread(story)
      else this.markRead(story)
    },
    /** Mark the stories newer or older than one story as read. */
    async markRelative(story: Story, direction: 'newer' | 'older') {
      if (!this.scope || this.isSaved) return
      await this.flushRead()
      try {
        const result = await api<{ counts: Record<string, FeedCounts> }>('POST', '/api/mark-read', {
          scope: this.scope, cutoff: story.story_date, direction,
        })
        for (const other of this.stories) {
          if (direction === 'older' ? other.story_date <= story.story_date : other.story_date >= story.story_date) other.read = true
        }
        useTree().applyCounts(result.counts)
      } catch (error) {
        useUi().error(errorMessage(error))
      }
    },
    /**
     * Mark a whole scope read, optionally only stories older than N days. Returns null on
     * failure; a whole feed or folder comes back with an undo token when anything was marked.
     */
    async markScope(scope: string, days = 0): Promise<MarkReadResult | null> {
      await this.flushRead()
      try {
        const result = await api<MarkReadResult>('POST', '/api/mark-read', { scope, days })
        useTree().applyCounts(result.counts)
        if (scope === this.scope || scope === 'all') {
          const limit = days ? Date.now() / 1000 - days * 86400 : Infinity
          const feeds = scope.startsWith('feed:') ? [Number(scope.slice(5))] : null
          for (const story of this.stories) {
            if (story.story_date <= limit && (!feeds || feeds.includes(story.feed_id))) story.read = true
          }
        }
        return result
      } catch (error) {
        useUi().error(errorMessage(error))
        return null
      }
    },
    /** Restore the read dates a markScope moved; the stories that are unread again are flipped here. */
    async undoMarkScope(undoToken: string) {
      try {
        const result = await api<MarkReadUndoResult>('POST', '/api/mark-read/undo', { undo_token: undoToken })
        useTree().applyCounts(result.counts)
        this.applyRead(result.story_ids, false)
        return true
      } catch (error) {
        useUi().error(errorMessage(error))
        return false
      }
    },

    // ---------------------------------------------------------- saved stories

    async save(story: Story, userTags?: string[]) {
      try {
        let result
        if (story.saved && story.saved_id !== null && userTags !== undefined) {
          result = await api('PATCH', `/api/saved/${story.saved_id}`, { user_tags: userTags })
        } else if (!story.saved) {
          result = await api('POST', '/api/saved', { story_id: story.id, user_tags: userTags })
        } else return
        this.applySaved(result)
      } catch (error) {
        useUi().error(errorMessage(error))
      }
    },
    async unsave(story: Story) {
      if (story.saved_id === null) return
      try {
        this.applySaved(await api('DELETE', `/api/saved/${story.saved_id}`))
      } catch (error) {
        useUi().error(errorMessage(error))
      }
    },
    applySaved(event: { story_id: number | null; saved_id: number; saved: boolean; user_tags: string[]; counts: SavedCounts }) {
      useTree().saved = event.counts
      const targets = [...this.stories, ...(this.selected ? [this.selected] : [])]
      for (const story of targets) {
        if ((event.story_id !== null && story.id === event.story_id) || story.saved_id === event.saved_id) {
          story.saved = event.saved
          story.saved_id = event.saved ? event.saved_id : this.isSaved ? story.saved_id : null
          story.user_tags = event.user_tags
        }
      }
      if (!event.saved && this.isSaved) this.stories = this.stories.filter((s) => s.saved_id !== event.saved_id)
    },

    // ---------------------------------------------------------- events from other tabs

    applyRead(ids: number[], read: boolean) {
      const set = new Set(ids)
      for (const story of this.stories) if (story.id !== null && set.has(story.id)) story.read = read
      if (this.selected && this.selected.id !== null && set.has(this.selected.id)) this.selected.read = read
    },
    applyFeedRead(feedId: number, markReadDate: number) {
      if (this.isSaved) return
      for (const story of this.stories) {
        if (story.feed_id === feedId && story.story_date <= markReadDate) story.read = true
      }
    },
    /** Whether the open live scope lists the feed's stories; All and folders leave out muted feeds. */
    inScope(feedId: number): boolean {
      if (!this.scope || this.isSaved) return false
      if (this.scope === `feed:${feedId}`) return true
      const feed = useTree().feedById.get(feedId)
      if (!feed || feed.muted) return false
      if (this.scope === 'all') return true
      const folder = this.scope.match(/^folder:(\d+)$/)
      return folder ? useTree().folderFeedIds(Number(folder[1])).includes(feedId) : false
    },
    /** New stories do not change the list; a bar offers them. */
    noteNewStories(feedId: number, count: number) {
      if (this.inScope(feedId) && this.query.trim().length < 2) this.newCount += count
    },
  },
})
