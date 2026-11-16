// Transient interface state (frontend/src/stores/ui.ts): toasts, the open dialog, the context menu.
import { defineStore } from 'pinia'

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'error'
  actionLabel?: string
  action?: () => void
}

/** The kind of action a menu item performs; it picks the icon color (`--tone-*` in styles.css). */
export type MenuTone =
  | 'read' | 'unread' | 'saved' | 'sync' | 'insight' | 'organize' | 'sound' | 'web' | 'account' | 'appearance'

export interface MenuItem {
  label: string
  icon?: string
  tone?: MenuTone
  action?: () => void
  checked?: boolean
  disabled?: boolean
  separator?: boolean
  danger?: boolean
}

let nextToast = 1

export const useUi = defineStore('ui', {
  state: () => ({
    toasts: [] as Toast[],
    dialog: null as { name: string; props: Record<string, any> } | null,
    menu: null as { x: number; y: number; items: MenuItem[] } | null,
    feedFilter: '',
    stylePopover: null as 'list' | 'story' | null,
    fullscreen: false,
    narrow: window.innerWidth < 900,
  }),
  actions: {
    toast(text: string, options: { kind?: 'info' | 'error'; actionLabel?: string; action?: () => void; seconds?: number } = {}) {
      const id = nextToast++
      this.toasts.push({ id, text, kind: options.kind ?? 'info', actionLabel: options.actionLabel, action: options.action })
      setTimeout(() => this.dismiss(id), (options.seconds ?? 5) * 1000)
      return id
    },
    error(text: string) {
      this.toast(text, { kind: 'error', seconds: 7 })
    },
    dismiss(id: number) {
      this.toasts = this.toasts.filter((t) => t.id !== id)
    },
    openDialog(name: string, props: Record<string, any> = {}) {
      this.menu = null
      this.stylePopover = null
      this.dialog = { name, props }
    },
    closeDialog() {
      this.dialog = null
    },
    openMenu(event: MouseEvent | { x: number; y: number }, items: MenuItem[]) {
      const x = 'clientX' in event ? event.clientX : event.x
      const y = 'clientY' in event ? event.clientY : event.y
      this.menu = { x, y, items }
    },
    /** Open a menu anchored to the element that was clicked (above it when near the bottom). */
    openMenuAt(element: HTMLElement, items: MenuItem[]) {
      const rect = element.getBoundingClientRect()
      this.menu = { x: rect.left, y: rect.bottom + 4, items }
    },
    closeMenu() {
      this.menu = null
    },
  },
})
