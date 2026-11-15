// Feed color derivations (frontend/src/utils/colors.ts), R8.5, R8.7, R8.8. Only favicon_color is
// stored; fade, border and text color are computed here with NewsBlur's formulas.

type Rgb = [number, number, number]

export function parseHex(hex: string | null | undefined): Rgb | null {
  if (!hex || !/^[0-9a-fA-F]{6}$/.test(hex)) return null
  return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)]
}

function css([r, g, b]: Rgb): string {
  return `rgb(${Math.round(r)}, ${Math.round(g)}, ${Math.round(b)})`
}

export function fade(rgb: Rgb): Rgb {
  return rgb.map((c) => Math.min(255, c + 30)) as Rgb
}

export function border(rgb: Rgb): Rgb {
  return rgb.map((c) => c * 0.75) as Rgb
}

function luminance([r, g, b]: Rgb): number {
  const channel = (v: number) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** White when the contrast against white is higher than against 50% grey, else black. */
export function textColor(rgb: Rgb): 'white' | 'black' {
  return contrast(rgb, [255, 255, 255]) > contrast(rgb, [128, 128, 128]) ? 'white' : 'black'
}

export interface FeedColors {
  base: string
  fade: string
  border: string
  text: string
  barStyle: Record<string, string>
}

/** Colors of a feed bar and strip. `dark` selects the stops of R8.8. Null when the feed has no color. */
export function feedColors(hex: string | null | undefined, dark: boolean): FeedColors | null {
  const rgb = parseHex(hex)
  if (!rgb) return null
  const top = dark ? rgb : fade(rgb)
  const bottom = dark ? border(rgb) : rgb
  const text = textColor(bottom)
  return {
    base: css(rgb),
    fade: css(fade(rgb)),
    border: css(border(rgb)),
    text,
    barStyle: {
      background: `linear-gradient(to bottom, ${css(top)}, ${css(bottom)})`,
      borderColor: css(border(rgb)),
      color: text,
    },
  }
}
