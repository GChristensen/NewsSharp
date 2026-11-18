// Session and initial load (frontend/src/stores/session.ts).
import { defineStore } from 'pinia'
import { api, ApiError, onUnauthorized } from '../api'
import type { Bootstrap } from '../types'
import { useEvents } from './events'
import { useSettings } from './settings'
import { useTree } from './tree'

export const useSession = defineStore('session', {
  state: () => ({
    status: 'loading' as 'loading' | 'login' | 'setup' | 'ready' | 'error',
    user: null as Bootstrap['user'] | null,
    worker: { alive: true, last_heartbeat: 0 },
    error: '',
  }),
  actions: {
    /** One request returns the user, preferences, view settings, tree and counts. */
    async bootstrap() {
      onUnauthorized(() => {
        if (this.status === 'ready') {
          useEvents().stop()
          this.status = 'login'
        }
      })
      try {
        const data = await api<Bootstrap>('GET', '/api/bootstrap', undefined, { quiet401: true })
        this.user = data.user
        this.worker = data.worker
        useSettings().load(data.preferences, data.view_settings)
        useTree().setTree(data.tree)
        this.status = 'ready'
        useEvents().start() // the event stream is opened after bootstrap
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          this.status = (error as any).data?.needs_setup ? 'setup' : 'login'
        } else {
          this.status = 'error'
          this.error = error instanceof Error ? error.message : String(error)
        }
      }
    },
    async login(username: string, password: string, setup: boolean) {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      await api('POST', setup ? '/api/setup' : '/api/login', { username, password, timezone }, { quiet401: true })
      await this.bootstrap()
    },
    async logout() {
      useEvents().stop()
      try {
        await api('POST', '/api/logout')
      } finally {
        window.location.assign('/')
      }
    },
  },
})
