// The responses list (contracts/overview.md): one row per assistant reply, its first non-empty
// line as the title; an explicit [+] shows a short summary; pressing the row jumps to the reply.
import type { Response } from '../types'

const plain = (line: string) =>
  line
    .replace(/^\s{0,3}(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)/, '')
    .replace(/\*\*|__|`/g, '')
    .trim()

export function titleOf(text: string): string {
  let isCode = false
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      isCode = !isCode
      continue
    }
    const shown = plain(line)
    if (!isCode && shown) return shown
  }
  return text.trim().split('\n')[0]?.trim() ?? ''
}

// The reply's words with whitespace collapsed; code fence markers are not words.
export const summaryOf = (text: string) =>
  text
    .split('\n')
    .filter(line => !/^\s*(```|~~~)/.test(line))
    .join(' ')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ')

// One row: a text longer than the room ends in …, so a row never wraps to a second line. A
// Button's own text does not truncate, so a list row clips what it puts in one.
export function clipRow(text: string, room: number) {
  const chars = [...text]
  return chars.length <= room ? text : `${chars.slice(0, Math.max(1, room - 1)).join('')}…`
}

// Words wrapped to `width`, at most `rows` rows, the last ending in … when cut.
export function wrapRows(text: string, width: number, rows: number): string[] {
  const out: string[] = []
  let line = ''
  for (const word of text.split(' ').filter(Boolean)) {
    const next = line ? `${line} ${word}` : word
    if (next.length <= width) {
      line = next
      continue
    }
    if (line) out.push(line)
    line = word.length > width ? word.slice(0, width) : word
  }
  if (line) out.push(line)
  if (out.length <= rows) return out
  const kept = out.slice(0, rows)
  const last = kept[rows - 1] ?? ''
  kept[rows - 1] = `${last.slice(0, Math.max(0, width - 1))}…`
  return kept
}

// Record a reply as the transcript draws it: the first block of a reply opens a row; a later
// block of the same reply is not a new row. Newest first, bounded.
export function remember(list: readonly Response[], id: string, text: string, limit = 200): Response[] {
  const title = titleOf(text)
  if (!title) return list as Response[]
  const summary = summaryOf(text)
  const known = list.find(r => r.id === id)
  if (known && known.title === title && known.summary === summary) return list as Response[]
  const entry = { id, title, summary }
  return known ? list.map(r => (r.id === id ? entry : r)) : [entry, ...list].slice(0, limit)
}
