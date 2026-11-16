// Live updates (frontend/src/stores/events.ts), item 21: an EventSource with manual reconnect and
// backoff, replay through the last event id, and fallback polling of the counts (R17.8).
import { defineStore } from 'pinia'
import { clientId } from '../api'
import { useSession } from './session'
import { useSettings } from './settings'
import { useStories } from './stories'
import { useTree } from './tree'

const BACKOFF = [1, 2, 5, 10, 30]

let source: EventSource | null = null
let reconnectTimer: number | undefined
let pollTimer: number | undefined
let hiddenAt = 0

export const useEvents = defineStore('events', {
  state: () => ({ connected: false, failures: 0, lastEventId: '', polling: false }),
  actions: {
    start() {
      this.connect()
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          hiddenAt = Date.now()
          return
        }
        if (!this.connected) {
          clearTimeout(reconnectTimer)
          this.connect() // R21.7: reconnect at once when the tab becomes visible
        } else if (hiddenAt && Date.now() - hiddenAt > 60000) {
          useTree().reloadCounts().catch(() => {})
        }
      })
    },
    stop() {
      source?.close()
      source = null
      clearTimeout(reconnectTimer)
      this.stopPolling()
      this.connected = false
    },
    connect() {
      source?.close()
      const url = this.lastEventId ? `/api/events?last_event_id=${this.lastEventId}` : '/api/events'
      const stream = new EventSource(url)
      source = stream
      stream.onopen = () => {
        const wasDown = this.failures > 0 || this.polling
        this.connected = true
        this.failures = 0
        this.stopPolling()
        if (wasDown || !this.lastEventId) {
          useTree().reloadCounts().catch(() => {})
          useStories().flushRead() // R6.8: retry queued reads when the stream reconnects
        }
      }
      stream.onerror = () => {
        if (source !== stream) return
        stream.close()
        this.connected = false
        this.failures++
        if (this.failures >= 3) this.startPolling()
        const delay = BACKOFF[Math.min(this.failures - 1, BACKOFF.length - 1)]
        clearTimeout(reconnectTimer)
        reconnectTimer = window.setTimeout(() => this.connect(), delay * 1000)
      }
      const on = (name: string, handler: (data: any) => void) =>
        stream.addEventListener(name, (event) => {
          const message = event as MessageEvent
          if (message.lastEventId) this.lastEventId = message.lastEventId
          try {
            handler(JSON.parse(message.data))
          } catch (error) {
            console.error('event handler failed', name, error)
          }
        })
      const tree = useTree()
      const stories = useStories()
      on('counts', (data) => tree.applyCounts(data.feeds))
      on('stories:read', (data) => stories.applyRead(data.ids, true))
      on('stories:unread', (data) => stories.applyRead(data.ids, false))
      on('feed:read', (data) => stories.applyFeedRead(data.feed_id, data.mark_read_date))
      on('saved', (data) => stories.applySaved(data))
      on('feed:new_stories', (data) => stories.noteNewStories(data.feed_id, data.count))
      on('feed:fetched', (data) => {
        const { feed_id, ...fields } = data
        delete fields.origin
        tree.updateFeed(feed_id, fields)
        tree.fetchedAt[feed_id] = Date.now()
        if (stories.pendingRefresh === feed_id) {
          stories.pendingRefresh = null
          stories.reload() // R17.10
        } else if (stories.scope === `feed:${feed_id}` && !stories.stories.length && !stories.loading) {
          stories.reload()
        }
      })
      on('feed:icon', (data) => tree.updateFeed(data.feed_id, { has_icon: true, icon_version: Date.now(), favicon_color: data.favicon_color }))
      on('feed:push', (data) => tree.updateFeed(data.feed_id, { is_push: data.is_push }))
      on('feed:settings', (data) => tree.updateFeed(data.feed_id, { use_proxy: data.use_proxy }))
      on('tree', (data) => {
        if (data.origin !== clientId) tree.reload().catch(() => {}) // R21.4
      })
      on('preferences', (data) => {
        if (data.origin !== clientId) useSettings().applyRemote(data.preferences, data.view_settings)
      })
      on('worker', (data) => (useSession().worker = data))
      on('resync', () => {
        tree.reload().catch(() => {})
        if (stories.scope) stories.reload()
      })
    },
    /** Without an event stream: poll the counts every 60 s, 10 s while feeds are unfetched. */
    startPolling() {
      if (this.polling) return
      this.polling = true
      const tick = async () => {
        const tree = useTree()
        try {
          if (tree.unfetched > 0) await tree.reload()
          else await tree.reloadCounts()
        } catch {
          /* next tick */
        }
        if (this.polling) pollTimer = window.setTimeout(tick, tree.unfetched > 0 ? 10000 : 60000)
      }
      pollTimer = window.setTimeout(tick, 1000)
    },
    stopPolling() {
      this.polling = false
      clearTimeout(pollTimer)
    },
  },
})
