// API types (frontend/src/types.ts), written by hand from the backend's JSON responses.

export interface Folder {
  id: number
  parent_id: number | null
  title: string
  position: number
  collapsed: boolean
}

export type FilterKind = 'promote' | 'demote' | 'hide'

/** Story filters: one case-insensitive regular expression per line, matched against title, author and summary. */
export type FeedFilters = Record<FilterKind, string[]>

export interface Feed {
  id: number
  title: string
  feed_title: string
  feed_address: string
  feed_link: string
  folder_id: number | null
  position: number
  unread: number
  /** Unread stories the Hide filter hides among the visible unread ones (no older than the oldest of
      them); they are not part of `unread`. Shown while the feed lists unread stories. */
  hidden: number
  /** Every stored story of the feed the Hide filter hides, read or not. Shown while the feed lists all stories. */
  hidden_all: number
  /** The feed's own story filters; the global ones (Prefs.filter_*) are appended to them. */
  filters: FeedFilters
  /** Left out of All Site Stories, folders and their counts; opened by itself it lists as usual. */
  muted: boolean
  favicon_color: string
  has_icon: boolean
  icon_version: number
  is_push: boolean
  /** Fetched through the proxy of config.yaml (a setting of the feed, shared by its subscribers). */
  use_proxy: boolean
  fetched_once: boolean
  has_exception: boolean
  exception_code: number
  exception_message: string
  errors_since_good: number
  last_update: number // UTC seconds of the latest fetch attempt; 0 before the first
}

/** The counts of one feed in every `counts` map (see Feed). */
export interface FeedCounts {
  unread: number
  hidden: number
  hidden_all: number
}

export interface SavedCounts {
  total: number
  feeds: Record<string, number>
  tags: Record<string, number>
  titles: Record<string, string>
}

export interface TreePayload {
  folders: Folder[]
  feeds: Feed[]
  saved: SavedCounts
}

export interface Story {
  id: number | null
  saved_id: number | null
  feed_id: number
  feed_title?: string
  title: string
  author: string
  permalink: string
  content?: string
  tags: string[]
  image_urls: string[]
  image_sigs: Record<string, string>
  story_date: number
  saved_at?: number
  read: boolean
  saved: boolean
  /** Matched by the Hide filter; such stories are listed only while hidden stories are shown. */
  hidden: boolean
  user_tags: string[]
  preview: string
  key: string // client side: "s<id>" or "v<saved id>"
}

/** POST /api/mark-read; `undo_token` is present for a whole feed or folder (null when nothing moved). */
export interface MarkReadResult {
  counts: Record<string, FeedCounts>
  undo_token?: string | null
}

/** POST /api/mark-read/undo */
export interface MarkReadUndoResult {
  counts: Record<string, FeedCounts>
  story_ids: number[]
}

export type View = 'original' | 'feed' | 'text' | 'story'
export type Layout = 'full' | 'split' | 'list' | 'grid' | 'magazine'
export type ViewKey = 'view' | 'layout' | 'order' | 'read_filter' | 'grid_columns' | 'show_content_preview' | 'image_preview' | 'story_display' | 'headline_indent' | 'summary_width'
export type Size = 'xs' | 's' | 'm' | 'l' | 'xl'
/** Variants of the dark theme: the data-tint blocks of styles.css. */
export type DarkTint = 'graphite' | 'oled' | 'ink' | 'navy' | 'warm' | 'moss' | 'teal' | 'plum' | 'ember' | 'rose' | 'dim'
/** A theme with its own color adjustments: the light theme or a tint of the dark theme. */
export type ThemeChoice = 'light' | DarkTint
/** The color adjustments of one theme (THEME_COLOR_RANGES in accounts/prefs.py); a missing one keeps the
    theme's own color, and bg_hue and bg_strength are set together. */
export type ThemeColorKey = 'accent_vividness' | 'accent_hue' | 'bg_hue' | 'bg_strength' | 'text_contrast'
export type ThemeColors = Partial<Record<ThemeColorKey, number>>

export interface Prefs {
  default_view: View
  story_layout: Layout
  default_order: 'newest' | 'oldest'
  default_read_filter: 'all' | 'unread'
  feed_pane_size: number
  sidebar_collapsed: boolean
  story_titles_pane_size: number
  story_pane_anchor: 'west' | 'north' | 'south'
  feed_view_single_story: boolean
  full_width_story: boolean
  story_position: 'left' | 'center' | 'right' | 'stretch'
  density: 'compact' | 'comfortable'
  show_content_preview: 'title' | 'small' | 'medium' | 'large'
  image_preview: 'none' | 'small-left' | 'small-right' | 'large-left' | 'large-right'
  grid_columns: number
  grid_height: Size
  /** Default maximum width of the content preview in List and Magazine, in pixels (per scope); 0 means no limit. */
  summary_max_width: number
  /** Default distance of the headlines from the left edge in List and Magazine, in pixels (per scope). */
  headline_indent: number
  story_display: 'standard' | 'images'
  story_styling: string
  story_size: Size
  story_line_spacing: Size
  feed_size: Size
  folder_counts: boolean
  title_counts: boolean
  sidebar_mode: 'all' | 'unread' | 'saved'
  show_feed_filter: boolean
  show_all_site_stories: boolean
  theme: 'auto' | 'light' | 'dark'
  dark_tint: DarkTint
  /** Color adjustments per theme (utils/themeColors.ts). */
  theme_colors: Partial<Record<ThemeChoice, ThemeColors>>
  dateformat: '12' | '24'
  timezone: string
  days_of_unread: number
  read_story_delay: number
  mark_read_on_scroll: boolean
  open_feed_action: 'newest' | 'none'
  start_page: 'home' | 'all' | 'last'
  markread_nextfeed: 'nextfeed' | 'nothing'
  mark_read_river_confirm: boolean
  new_window: boolean
  keyboard_verticalarrows: 'story' | 'scroll'
  keyboard_horizontalarrows: 'view' | 'site'
  space_bar_action: 'next_unread' | 'scroll_only'
  space_scroll_spacing: number
  arrow_scroll_spacing: number
  animations: boolean
  show_tooltips: boolean
  feedxcavator_url: string
  feedxcavator_newspaper_url: string
  share_services: string[]
  /** Story filters: one case-insensitive regular expression per line, matched against title, author and summary. */
  filter_promote: string[]
  filter_demote: string[]
  filter_hide: string[]
}

export interface Bootstrap {
  user: { id: number; username: string; is_admin: boolean }
  preferences: Prefs
  view_settings: Record<string, Record<string, string>>
  tree: TreePayload
  worker: { alive: boolean; last_heartbeat: number }
  version: string
}

export interface TextResult {
  text: string | null
  url: string
  failed: boolean
  reason: string
}

export interface FetchLogEntry {
  at: number
  code: number
  message: string
  duration_ms: number
}

export interface FeedSettings {
  /** Whether config.yaml sets proxy.url; without it a feed set to use the proxy is fetched directly. */
  proxy_available: boolean
  /** Where the stored icon was downloaded from; empty when the feed has none. */
  icon_url: string
  /** The icon address set by hand, which icon searches fetch instead of looking the icon up; empty when none. */
  icon_link: string
  fetch_history: FetchLogEntry[]
}

/** POST /api/feeds/<id>/icon/fetch: the search's outcome, or `pending` when it outlasted the wait. */
export type IconFetchResult =
  | { pending: true }
  | { pending?: undefined; found: boolean; icon_url: string; favicon_color: string; tried: number; out_of_time: boolean }

export interface Statistics {
  feed: { id: number; title: string; feed_address: string; feed_link: string; has_exception: boolean; exception_code: number }
  last_update: number
  next_update: number
  interval_minutes: number
  reason: string
  push: { hub: string; verified: boolean; lease_expires: number; last_push_at: number } | null
  stories_last_month: number
  average_stories_per_month: number
  months: [string, number][]
  weekdays: number[]
  hours: number[]
  fetch_history: FetchLogEntry[]
  push_history: FetchLogEntry[]
  story_count: number
  content_bytes: number
  last_load_ms: number
}
