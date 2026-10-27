// Content an agent surfaced into the pane with the `surface` control (contracts/overview.md).
// Only entries the writer verified by read-back are drawn, resolved ones never.
import type { SurfacedItem } from '../types'

export const DEFAULT_BADGES: Record<string, string> = { claude: '✳', codex: 'C' }

const printable = (value: unknown, limit: number) =>
  typeof value === 'string' && value.length >= 1 && [...value].length <= limit && !/[\x00-\x1f\x7f-\x9f]/.test(value) ? value : undefined

// HARNESS_UI_BADGES: a JSON object of provider name to badge; invalid input is ignored.
export function parseBadges(text: string | undefined): Record<string, string> {
  if (!text) return {}
  try {
    const value = JSON.parse(text)
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value).flatMap(([key, badge]) => (printable(badge, 8) ? [[key, badge as string]] : [])))
  } catch {
    return {}
  }
}

export function badgeFor(entry: { badge?: unknown; surfaced_by?: unknown }, overrides: Record<string, string> = {}): string {
  const own = printable(entry.badge, 8)
  if (own) return own
  const by = typeof entry.surfaced_by === 'string' ? entry.surfaced_by : ''
  return overrides[by] ?? DEFAULT_BADGES[by] ?? (by.slice(0, 1).toUpperCase() || '•')
}

const clean = (value: unknown, limit: number) => (typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f-\x9f]+/g, ' ').trim().slice(0, limit) : '')

export function openSurfaced(record: Record<string, unknown> | undefined, overrides: Record<string, string> = {}): SurfacedItem[] {
  const list = Array.isArray(record?.surfaced) ? (record!.surfaced as unknown[]) : []
  return list.slice(0, 200).flatMap(raw => {
    if (!raw || typeof raw !== 'object') return []
    const entry = raw as Record<string, unknown>
    const kind = entry.kind
    if (entry.verified !== true || entry.status === 'resolved') return []
    if (kind !== 'ask' && kind !== 'note' && kind !== 'decision') return []
    const text = clean(entry.text, 500)
    const id = clean(entry.id, 128)
    if (!text || !id) return []
    return [{ id, kind, text, badge: badgeFor(entry, overrides), by: clean(entry.surfaced_by, 40) }]
  })
}

// Case, punctuation and spacing ignored: how a captured ask is matched to a surfaced one.
export const sameText = (a: string, b: string) => a.toLowerCase().replace(/\W+/g, ' ').trim() === b.toLowerCase().replace(/\W+/g, ' ').trim()
