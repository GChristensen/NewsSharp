// Tests of the theme color adjustments (frontend/src/utils/themeColors.test.ts).
import { describe, expect, it } from 'vitest'
import type { ThemeChoice } from '../types'
import { THEME_CASTS, themeColorProperties, withAdjustment, withoutAdjustment } from './themeColors'

/** The background tokens the background sliders replace (styles.css, the color adjustment block). */
const BACKGROUND = ['bg', 'bg-pane', 'bg-bar', 'bg-hover', 'border', 'border-subtle', 'scrollbar-thumb', 'border-strong']

/** A hex color in OKLAB (Björn Ottosson's matrices, as browsers use them). */
function oklab(hex: string): [number, number, number] {
  const linear = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  const [r, g, b] = linear
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

/** The --base-* background colors of each theme block of styles.css. Vitest empties CSS imports, so the
    file is read through Node (imported by a computed name: the project has no Node type declarations). */
async function themeBlocks(): Promise<Map<ThemeChoice, [number, number, number][]>> {
  const fs = (await import('node:fs' as string)) as { readFileSync(path: URL, encoding: 'utf-8'): string }
  const css = fs.readFileSync(new URL('../styles.css', import.meta.url), 'utf-8')
  const blocks = new Map<ThemeChoice, [number, number, number][]>()
  for (const [, selector, body] of css.matchAll(/(:root[^{]*)\{([^}]*)\}/g)) {
    if (!/--base-bg-pane:\s*#/.test(body)) continue
    const tint = /data-tint='(\w+)'/.exec(selector)?.[1]
    const theme = (tint ?? (selector.includes("'light'") ? 'light' : 'graphite')) as ThemeChoice
    const colors = [...body.matchAll(/--base-([a-z-]+):\s*(#[0-9a-f]{6})/g)].filter(([, name]) => BACKGROUND.includes(name))
    blocks.set(theme, colors.map(([, , hex]) => oklab(hex)))
  }
  return blocks
}

describe('theme casts', () => {
  it('match a fit of the background colors in styles.css', async () => {
    const blocks = await themeBlocks()
    expect([...blocks.keys()].sort()).toEqual(Object.keys(THEME_CASTS).sort())
    for (const [theme, colors] of blocks) {
      // styles.css: chroma = strength * 0.0035 * distance of the lightness from the ground.
      const distance = colors.map(([l]) => (theme === 'light' ? 1 - l : l))
      const chroma = colors.map(([, a, b]) => Math.hypot(a, b))
      const k = chroma.reduce((sum, c, i) => sum + c * distance[i], 0) / distance.reduce((sum, w) => sum + w * w, 0)
      const a = colors.reduce((sum, [, x]) => sum + x, 0)
      const b = colors.reduce((sum, [, , y]) => sum + y, 0)
      const hue = Math.hypot(a, b) > 1e-4 ? ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360 : 250
      expect(THEME_CASTS[theme].strength, theme).toBe(Math.round(k / 0.0035))
      expect(THEME_CASTS[theme].hue, theme).toBe(Math.round(hue))
    }
  })
})

describe('theme color adjustments', () => {
  it('store both background sliders when one moves, and drop both on reset', () => {
    const moved = withAdjustment('oled', { text_contrast: 5 }, 'bg_strength', 30)
    expect(moved).toEqual({ text_contrast: 5, bg_strength: 30, bg_hue: THEME_CASTS.oled.hue })
    expect(withoutAdjustment(moved, 'bg_hue')).toEqual({ text_contrast: 5 })
    expect(withoutAdjustment({ accent_hue: 4 }, 'accent_hue')).toBeUndefined()
  })
  it('become the --light-* and --dark-* properties', () => {
    const properties = themeColorProperties({ light: { accent_hue: -4 }, moss: { bg_hue: 120, bg_strength: 20 }, navy: { text_contrast: 9 } }, 'moss')
    expect(properties).toEqual({ '--light-accent-hue': '-4', '--dark-bg-hue': '120', '--dark-bg-strength': '20', '--dark-bg-set': '1' })
  })
})
