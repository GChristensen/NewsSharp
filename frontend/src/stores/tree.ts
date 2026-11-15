// The feed and folder tree (frontend/src/stores/tree.ts), item 7: order, counts, sidebar modes,
// optimistic moves and deletes with undo.
import { defineStore } from 'pinia'
import { api, errorMessage } from '../api'
import type { Feed, Folder, SavedCounts, TreePayload } from '../types'
import { useSettings } from './settings'
import { useUi } from './ui'

export type TreeItem = { kind: 'feed'; feed: Feed } | { kind: 'folder'; folder: Folder }

function itemPosition(item: TreeItem): number {
  return item.kind === 'feed' ? item.feed.position : item.folder.position
}

export const useTree = defineStore('tree', {
  state: () => ({
    folders: [] as Folder[],
    feeds: [] as Feed[],
    saved: { total: 0, feeds: {}, tags: {}, titles: {} } as SavedCounts,
    openFeedId: null as number | null, // stays visible in Unread mode until another feed is opened
    progressDismissed: false,
    progressTotal: 0,
  }),
  getters: {
    feedById(state): Map<number, Feed> {
      return new Map(state.feeds.map((f) => [f.id, f]))
    },
    folderById(state): Map<number, Folder> {
      return new Map(state.folders.map((f) => [f.id, f]))
    },
    childMap(state): Map<number | null, TreeItem[]> {
      const map = new Map<number | null, TreeItem[]>()
      const push = (parent: number | null, item: TreeItem) => {
        if (!map.has(parent)) map.set(parent, [])
        map.get(parent)!.push(item)
      }
      for (const folder of state.folders) push(folder.parent_id, { kind: 'folder', folder })
      for (const feed of state.feeds) push(feed.folder_id, { kind: 'feed', feed })
      for (const items of map.values()) {
        items.sort((a, b) => itemPosition(a) - itemPosition(b) || (a.kind === 'folder' ? -1 : 1))
      }
      return map
    },
    /** Unread stories of All Site Stories: muted feeds do not count. */
    totalUnread(state): number {
      return state.feeds.reduce((sum, f) => sum + (f.muted ? 0 : f.unread), 0)
    },
    unfetched(state): number {
      return state.feeds.filter((f) => !f.fetched_once && !f.has_exception).length
    },
    /** Feeds in display order (depth first), for next / previous feed navigation. */
    feedOrder(): Feed[] {
      const result: Feed[] = []
      const walk = (parent: number | null) => {
        for (const item of this.childMap.get(parent) ?? []) {
          if (item.kind === 'feed') result.push(item.feed)
          else walk(item.folder.id)
        }
      }
      walk(null)
      return result
    },
    orphanSavedFeeds(state): { id: number; title: string; count: number }[] {
      return Object.entries(state.saved.titles).map(([id, title]) => ({ id: Number(id), title, count: state.saved.feeds[id] ?? 0 }))
    },
  },
  actions: {
    setTree(payload: TreePayload) {
      this.folders = payload.folders
      this.feeds = payload.feeds
      this.saved = payload.saved
      const waiting = this.unfetched
      if (waiting > this.progressTotal) this.progressTotal = waiting
      if (waiting === 0) {
        this.progressTotal = 0
        this.progressDismissed = false
      }
    },
    async reload() {
      this.setTree(await api<TreePayload>('GET', '/api/tree'))
    },
    async reloadCounts() {
      const counts = await api<{ feeds: Record<string, number>; saved: SavedCounts }>('GET', '/api/counts')
      this.applyCounts(counts.feeds)
      this.saved = counts.saved
    },
    applyCounts(counts: Record<string, number>) {
      for (const [id, unread] of Object.entries(counts)) {
        const feed = this.feedById.get(Number(id))
        if (feed) feed.unread = unread
      }
    },
    adjustUnread(feedId: number, delta: number) {
      const feed = this.feedById.get(feedId)
      if (feed) feed.unread = Math.max(0, feed.unread + delta)
    },
    children(parent: number | null): TreeItem[] {
      return this.childMap.get(parent) ?? []
    },
    folderFeedIds(folderId: number): number[] {
      const ids: number[] = []
      const walk = (parent: number) => {
        for (const item of this.children(parent)) {
          if (item.kind === 'feed') ids.push(item.feed.id)
          else walk(item.folder.id)
        }
      }
      walk(folderId)
      return ids
    },
    /**
     * The number shown on a feed row: unread stories, or saved stories in Saved for Later mode. A
     * muted feed shows no unread count; its saved stories still count.
     */
    feedCount(feed: Feed): number {
      if (useSettings().prefs.sidebar_mode === 'saved') return this.saved.feeds[feed.id] ?? 0
      return feed.muted ? 0 : feed.unread
    },
    folderCount(folderId: number): number {
      return this.folderFeedIds(folderId).reduce((sum, id) => sum + this.feedCount(this.feedById.get(id)!), 0)
    },
    /** Unread stories of a folder's story list, which leaves out muted feeds. */
    folderUnread(folderId: number): number {
      return this.folderFeedIds(folderId).reduce((sum, id) => {
        const feed = this.feedById.get(id)!
        return sum + (feed.muted ? 0 : feed.unread)
      }, 0)
    },
    feedVisible(feed: Feed): boolean {
      const filter = useUi().feedFilter.trim().toLowerCase()
      if (filter && !feed.title.toLowerCase().includes(filter)) return false
      const mode = useSettings().prefs.sidebar_mode
      if (mode === 'unread') return (feed.unread > 0 && !feed.muted) || feed.id === this.openFeedId
      if (mode === 'saved') return (this.saved.feeds[feed.id] ?? 0) > 0
      return true
    },
    /** R7.4: the rows of a container under the sidebar mode and the feed filter. */
    visibleChildren(parent: number | null): TreeItem[] {
      const filtering = useUi().feedFilter.trim() !== '' || useSettings().prefs.sidebar_mode !== 'all'
      return this.children(parent).filter((item) => {
        if (item.kind === 'feed') return this.feedVisible(item.feed)
        if (!filtering) return true
        return this.folderFeedIds(item.folder.id).some((id) => this.feedVisible(this.feedById.get(id)!))
      })
    },
    isDescendant(folderId: number, ancestorId: number): boolean {
      let current: number | null = folderId
      while (current !== null) {
        if (current === ancestorId) return true
        current = this.folderById.get(current)?.parent_id ?? null
      }
      return false
    },
    /** R7.7: apply a move locally at once, send one operation, restore on failure. */
    async move(item: TreeItem, parentId: number | null, position: number) {
      const before = { folders: this.folders.map((f) => ({ ...f })), feeds: this.feeds.map((f) => ({ ...f })) }
      const same = (other: TreeItem) =>
        other.kind === item.kind && (other.kind === 'feed' ? other.feed.id === (item as any).feed.id : other.folder.id === (item as any).folder.id)
      const oldParent = item.kind === 'feed' ? item.feed.folder_id : item.folder.parent_id
      const target = this.children(parentId).filter((other) => !same(other))
      position = Math.max(0, Math.min(position, target.length))
      target.splice(position, 0, item)
      if (item.kind === 'feed') item.feed.folder_id = parentId
      else item.folder.parent_id = parentId
      const renumber = (items: TreeItem[]) =>
        items.forEach((entry, index) => {
          if (entry.kind === 'feed') entry.feed.position = index
          else entry.folder.position = index
        })
      renumber(target)
      if (oldParent !== parentId) renumber(this.children(oldParent))
      const ref = item.kind === 'feed' ? `feed:${item.feed.id}` : `folder:${item.folder.id}`
      try {
        await api('POST', '/api/tree/move', { item: ref, parent_id: parentId, position })
      } catch (error) {
        this.folders = before.folders
        this.feeds = before.feeds
        useUi().error(`The move failed: ${errorMessage(error)}`)
      }
    },
    async setCollapsed(folder: Folder, collapsed: boolean) {
      folder.collapsed = collapsed
      try {
        await api('PATCH', `/api/folders/${folder.id}`, { collapsed })
      } catch {
        /* the state is cosmetic; the next tree load corrects it */
      }
    },
    async renameFeed(feed: Feed, title: string) {
      await api('PATCH', `/api/subscriptions/${feed.id}`, { title })
      await this.reload()
    },
    /** Mute or unmute a feed at once; restored on failure. Other tabs reload the tree. */
    async setMuted(feed: Feed, muted: boolean) {
      feed.muted = muted
      try {
        await api('PATCH', `/api/subscriptions/${feed.id}`, { muted })
      } catch (error) {
        feed.muted = !muted
        useUi().error(`${muted ? 'Muting' : 'Unmuting'} failed: ${errorMessage(error)}`)
        throw error
      }
    },
    async renameFolder(folder: Folder, title: string) {
      await api('PATCH', `/api/folders/${folder.id}`, { title })
      folder.title = title
    },
    async createFolder(title: string, parentId: number | null) {
      await api('POST', '/api/folders', { title, parent_id: parentId })
      await this.reload()
    },
    /** R7.10: no confirmation; the row disappears and an Undo toast is shown for 10 s. */
    async remove(item: TreeItem) {
      const name = item.kind === 'feed' ? item.feed.title : item.folder.title
      const url = item.kind === 'feed' ? `/api/subscriptions/${item.feed.id}` : `/api/folders/${item.folder.id}`
      try {
        const { undo_token } = await api<{ undo_token: string }>('DELETE', url)
        await this.reload()
        useUi().toast(`Deleted ${name}`, {
          seconds: 10,
          actionLabel: 'Undo',
          action: async () => {
            try {
              this.setTree(await api<TreePayload>('POST', '/api/undo', { undo_token }))
            } catch (error) {
              useUi().error(errorMessage(error))
            }
          },
        })
      } catch (error) {
        useUi().error(`Delete failed: ${errorMessage(error)}`)
      }
    },
    updateFeed(feedId: number, fields: Partial<Feed>) {
      const feed = this.feedById.get(feedId)
      if (!feed) return
      const wasCustomTitle = feed.title !== feed.feed_title && feed.feed_title !== ''
      if (fields.title !== undefined) {
        fields = { ...fields, feed_title: fields.title }
        if (wasCustomTitle) delete fields.title
      }
      Object.assign(feed, fields)
      if (this.unfetched === 0) {
        this.progressTotal = 0
        this.progressDismissed = false
      }
    },
  },
})
