// Color adjustments of the themes (frontend/src/utils/themeColors.ts): the sliders of the Theme tab and
// the custom properties that carry them to styles.css. The web process writes the same properties into
// the html tag (theme_color_style in api/views.py) and validates the ranges (accounts/prefs.py).
import type { DarkTint, ThemeChoice, ThemeColorKey, ThemeColors } from '../types'

export interface Adjustment {
  key: ThemeColorKey
  label: string
  min: number
  max: number
  unit: string
  hint: string
  /** The track shows the color wheel. */
  hue?: boolean
}

export const ADJUSTMENTS: Adjustment[] = [
  { key: 'accent_vividness', label: 'Highlight vividness', min: 30, max: 170, unit: '%', hint: 'Accent, links and selection: muted to vivid' },
  { key: 'accent_hue', label: 'Highlight hue', min: -30, max: 30, unit: '°', hint: 'Turns the accent towards a neighbouring color' },
  { key: 'bg_strength', label: 'Background color', min: 0, max: 100, unit: '', hint: 'Of backgrounds and borders; 0 is pure grey' },
  { key: 'bg_hue', label: 'Background hue', min: 0, max: 359, unit: '°', hint: 'Shows once the background color is above 0', hue: true },
  { key: 'text_contrast', label: 'Text contrast', min: -50, max: 50, unit: '', hint: 'All text: softer to crisper' },
]

/** The value a slider shows while the theme has none stored: the accent sliders and the text are neutral,
    the background sliders show the theme's own cast. */
const NEUTRAL: Record<'accent_vividness' | 'accent_hue' | 'text_contrast', number> = { accent_vividness: 100, accent_hue: 0, text_contrast: 0 }

/** The background cast of each theme as the hue and strength of the background sliders: a least-squares
    fit of the background and border colors of its block in styles.css (checked by utils.test.ts).
    Setting these values reproduces the theme closely. */
export const THEME_CASTS: Record<ThemeChoice, { hue: number; strength: number }> = {
  light: { hue: 259, strength: 22 },
  graphite: { hue: 286, strength: 7 },
  oled: { hue: 250, strength: 0 },
  ink: { hue: 261, strength: 26 },
  navy: { hue: 267, strength: 43 },
  warm: { hue: 72, strength: 10 },
  moss: { hue: 155, strength: 15 },
  teal: { hue: 217, strength: 18 },
  plum: { hue: 302, strength: 30 },
  ember: { hue: 18, strength: 6 },
  rose: { hue: 344, strength: 13 },
  dim: { hue: 267, strength: 12 },
}

/** Themes whose accent is grey: vividness and hue have nothing to act on. */
export const GREY_ACCENT: ThemeChoice[] = ['oled']

export function sliderValue(theme: ThemeChoice, colors: ThemeColors | undefined, key: ThemeColorKey): number {
  const stored = colors?.[key]
  if (stored !== undefined) return stored
  if (key === 'bg_hue') return THEME_CASTS[theme].hue
  if (key === 'bg_strength') return THEME_CASTS[theme].strength
  return NEUTRAL[key]
}

/** The adjustments of a theme after one slider moved. Moving either background slider stores both, the
    other one at its shown value, since the background color is only replaced as a whole. */
export function withAdjustment(theme: ThemeChoice, colors: ThemeColors | undefined, key: ThemeColorKey, value: number): ThemeColors {
  const next: ThemeColors = { ...colors, [key]: value }
  if (key === 'bg_hue' || key === 'bg_strength') {
    next.bg_hue = sliderValue(theme, next, 'bg_hue')
    next.bg_strength = sliderValue(theme, next, 'bg_strength')
  }
  return next
}

/** The adjustments of a theme after one slider was reset; undefined when none is left. */
export function withoutAdjustment(colors: ThemeColors | undefined, key: ThemeColorKey): ThemeColors | undefined {
  const next: ThemeColors = { ...colors }
  delete next[key]
  if (key === 'bg_hue' || key === 'bg_strength') {
    delete next.bg_hue
    delete next.bg_strength
  }
  return Object.keys(next).length ? next : undefined
}

/** The --light-* and --dark-* properties of styles.css for the light theme and a dark tint. */
export function themeColorProperties(all: Partial<Record<ThemeChoice, ThemeColors>>, darkTint: DarkTint): Record<string, string> {
  const properties: Record<string, string> = {}
  for (const [mode, theme] of [['light', 'light'], ['dark', darkTint]] as const) {
    const colors = all[theme] ?? {}
    for (const [key, value] of Object.entries(colors)) properties[`--${mode}-${key.replace(/_/g, '-')}`] = String(value)
    if (colors.bg_hue !== undefined) properties[`--${mode}-bg-set`] = '1'
  }
  return properties
}

/** Every property themeColorProperties can set, so that stale ones can be removed. */
export const THEME_COLOR_PROPERTIES = (['light', 'dark'] as const).flatMap((mode) =>
  [...ADJUSTMENTS.map((adjustment) => adjustment.key), 'bg_set'].map((key) => `--${mode}-${key.replace(/_/g, '-')}`),
)
