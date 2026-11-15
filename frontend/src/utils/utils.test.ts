// Unit tests of the pure helpers (frontend/src/utils/utils.test.ts).
import { describe, expect, it } from 'vitest'
import { feedColors, parseHex, textColor } from './colors'
import { highlight, previewText, storyDate, subscribeUrlFromLocation } from './format'
import { firstVideo } from './media'

describe('feed colors (R8.5, R8.8)', () => {
  it('derives fade, border and text color', () => {
    const colors = feedColors('487bae', false)!
    expect(colors.base).toBe('rgb(72, 123, 174)')
    expect(colors.fade).toBe('rgb(102, 153, 204)')
    expect(colors.border).toBe('rgb(54, 92, 131)')
    expect(textColor(parseHex('111111')!)).toBe('white')
    expect(textColor(parseHex('ffe054')!)).toBe('black')
  })
  it('uses the border color as the bottom stop in the dark theme', () => {
    expect(feedColors('487bae', true)!.barStyle.background).toContain('rgb(54, 92, 131)')
    expect(feedColors('', true)).toBeNull()
  })
})

describe('subscribe URL (R20.10)', () => {
  it('accepts raw and encoded URLs', () => {
    const expected = 'https://example.com/feed.xml?format=rss&x=1'
    expect(subscribeUrlFromLocation('/subscribe/https://example.com/feed.xml?format=rss&x=1')).toBe(expected)
    expect(subscribeUrlFromLocation('/subscribe/https%3A%2F%2Fexample.com%2Ffeed.xml%3Fformat%3Drss%26x%3D1')).toBe(expected)
  })
  it('repairs a collapsed slash and a missing scheme', () => {
    expect(subscribeUrlFromLocation('/subscribe/https:/example.com/feed')).toBe('https://example.com/feed')
    expect(subscribeUrlFromLocation('/subscribe/example.com/feed')).toBe('https://example.com/feed')
  })
})

describe('text helpers', () => {
  it('highlights search terms in escaped text (R12.7)', () => {
    expect(highlight('Linux <kernel>', ['linux'])).toBe('<mark>Linux</mark> &lt;kernel&gt;')
  })
  it('omits short previews in rows but not in cards (R9.7)', () => {
    expect(previewText('twenty characters ok', 'Title', 'medium', false)).toBe('')
    expect(previewText('twenty characters ok', 'Title', 'medium', true)).toBe('twenty characters ok')
    expect(previewText('x'.repeat(900), 'Title', 'small', false)).toHaveLength(300)
  })
  it('formats story dates in the user time zone (R9.5)', () => {
    const now = new Date('2026-11-15T12:00:00Z')
    expect(storyDate(Date.parse('2026-11-15T09:30:00Z') / 1000, 'UTC', false, now)).toBe('09:30')
    expect(storyDate(Date.parse('2026-11-14T23:30:00Z') / 1000, 'UTC', false, now)).toMatch(/^Yesterday, 23:30/)
    expect(storyDate(Date.parse('2026-11-14T23:30:00Z') / 1000, 'Europe/Berlin', false, now)).toBe('00:30')
    expect(storyDate(Date.parse('2025-03-02T10:00:00Z') / 1000, 'UTC', false, now)).toContain('2025')
  })
})

describe('grid card video (R9.10)', () => {
  it('prefers an MP4 among video and source URLs', () => {
    const content =
      '<p>x</p><video src="https://a.example/v.webm"></video>' +
      '<video><source src="https://a.example/w.mp4?x=1&amp;y=2" type="video/mp4"></video>'
    expect(firstVideo(content)).toBe('https://a.example/w.mp4?x=1&y=2')
  })

  it('falls back to the first video URL', () => {
    expect(firstVideo('<video poster="https://a.example/p.jpg" src="https://a.example/v.webm"></video>')).toBe(
      'https://a.example/v.webm',
    )
  })

  it('returns null without a playable video', () => {
    expect(firstVideo('<video poster="https://a.example/p.jpg"></video>')).toBeNull()
    expect(firstVideo('<p>no video</p>')).toBeNull()
    expect(firstVideo(undefined)).toBeNull()
  })
})
