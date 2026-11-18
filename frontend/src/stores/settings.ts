// Preferences and per-scope view settings (frontend/src/stores/settings.ts), item 19.
import { defineStore } from 'pinia'
import { api, errorMessage } from '../api'
import type { Layout, Prefs, ThemeChoice, View, ViewKey } from '../types'
import { THEME_COLOR_PROPERTIES, themeColorProperties } from '../utils/themeColors'
import { useUi } from './ui'

const DEFAULT_KEYS: Record<ViewKey, keyof Prefs> = {
  view: 'default_view',
  layout: 'story_layout',
  order: 'default_order',
  read_filter: 'default_read_filter',
  grid_columns: 'grid_columns',
  show_content_preview: 'show_content_preview',
  image_preview: 'image_preview',
  story_display: 'story_display',
  headline_indent: 'headline_indent',
  summary_width: 'summary_max_width',
}

let saveTimer: number | undefined
let pending: Partial<Prefs> = {}
const media = window.matchMedia('(prefers-color-scheme: dark)')

/** The view-settings target of a scope: every saved scope shares the target "saved". */
export function settingsScope(scope: string): string {
  return scope.startsWith('saved') ? 'saved' : scope
}

export const useSettings = defineStore('settings', {
  state: () => ({
    prefs: {} as Prefs,
    viewSettings: {} as Record<string, Record<string, string>>,
    systemDark: media.matches,
  }),
  getters: {
    dark(state): boolean {
      return state.prefs.theme === 'dark' || (state.prefs.theme === 'auto' && state.systemDark)
    },
    hour12(state): boolean {
      return state.prefs.dateformat === '12'
    },
  },
  actions: {
    load(prefs: Prefs, viewSettings: Record<string, Record<string, string>>) {
      this.prefs = prefs
      this.viewSettings = viewSettings
      this.applyTheme()
      media.addEventListener('change', (event) => {
        this.systemDark = event.matches // R20.7: reacts while the page is open
        this.applyTheme()
      })
    },
    /** Put the theme, its color adjustments and the size preferences on the html element. `preview` shows
        another theme with its own adjustments (the theme picker while a square is hovered). */
    applyTheme(preview?: ThemeChoice) {
      const root = document.documentElement
      const dark = preview ? preview !== 'light' : this.dark
      const tint = preview && preview !== 'light' ? preview : this.prefs.dark_tint
      root.dataset.theme = dark ? 'dark' : 'light'
      root.dataset.tint = tint
      const properties = themeColorProperties(this.prefs.theme_colors, tint)
      for (const name of THEME_COLOR_PROPERTIES) {
        if (name in properties) root.style.setProperty(name, properties[name])
        else root.style.removeProperty(name)
      }
      const reduce = !this.prefs.animations || window.matchMedia('(prefers-reduced-motion: reduce)').matches
      root.dataset.animations = reduce ? 'off' : 'on'
      for (const key of ['story_size', 'story_line_spacing', 'feed_size', 'grid_height', 'density'] as const) {
        root.dataset[key.replace(/_(\w)/g, (_, c) => c.toUpperCase())] = String(this.prefs[key])
      }
    },
    /** Apply a preference locally at once and save it debounced by 500 ms (R19.8). */
    set<K extends keyof Prefs>(key: K, value: Prefs[K]) {
      if (this.prefs[key] === value) return
      this.prefs[key] = value
      pending[key] = value
      this.applyTheme()
      clearTimeout(saveTimer)
      saveTimer = window.setTimeout(() => this.flush(), 500)
    },
    async flush() {
      const changes = pending
      pending = {}
      if (!Object.keys(changes).length) return
      try {
        await api('PATCH', '/api/preferences', changes)
      } catch (error) {
        useUi().error(`Settings were not saved: ${errorMessage(error)}`)
      }
    },
    /** Preferences announced by another tab (item 21). Local unsaved changes win. */
    applyRemote(prefs: Prefs, viewSettings?: Record<string, Record<string, string>>) {
      this.prefs = { ...prefs, ...pending }
      if (viewSettings) this.viewSettings = viewSettings
      this.applyTheme()
    },
    stored(scope: string, key: ViewKey): string | undefined {
      return this.viewSettings[settingsScope(scope)]?.[key]
    },
    /** R19.2: the stored value for the scope, else the global default, with NewsBlur's overrides. */
    effective(scope: string | null, key: ViewKey): string {
      if (scope) {
        if (key === 'read_filter' && scope.startsWith('saved')) return 'all'
        const stored = this.stored(scope, key)
        if (stored) return stored
        if (key === 'read_filter' && (scope === 'all' || scope.startsWith('folder:'))) return 'unread'
      }
      return String(this.prefs[DEFAULT_KEYS[key]])
    },
    layout(scope: string | null): Layout {
      return this.effective(scope, 'layout') as Layout
    },
    view(scope: string | null): View {
      return this.effective(scope, 'view') as View
    },
    /** Store a setting for a scope; `save: false` only applies it locally (a slider being dragged). */
    async setView(scope: string, key: ViewKey, value: string, save = true) {
      const target = settingsScope(scope)
      this.viewSettings = { ...this.viewSettings, [target]: { ...this.viewSettings[target], [key]: value } }
      if (!save) return
      try {
        await api('PUT', '/api/view-settings', { scope: target, key, value })
      } catch (error) {
        useUi().error(`The setting was not saved: ${errorMessage(error)}`)
      }
    },
    /** "Use default": delete the override of one scope (R19.3). */
    async clearView(scope: string, key: ViewKey) {
      const target = settingsScope(scope)
      const copy = { ...this.viewSettings[target] }
      delete copy[key]
      this.viewSettings = { ...this.viewSettings, [target]: copy }
      try {
        await api('DELETE', '/api/view-settings', { scope: target, key })
      } catch (error) {
        useUi().error(`The setting was not saved: ${errorMessage(error)}`)
      }
    },
    /** R19.4: reset one setting for all scopes. */
    async resetAll(key: ViewKey) {
      const result = await api('DELETE', `/api/view-settings?key=${key}`)
      this.viewSettings = result.view_settings
    },
  },
})
