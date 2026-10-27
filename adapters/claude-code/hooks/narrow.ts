// Markdown tables don't fit a 44-column pane: the renderer wraps every cell and the grid
// falls apart. In the pane a table becomes one list line per row: the
// first cell, then each other cell labeled with its header. Code blocks are left alone.

const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split(/(?<!\\)\|/)
    .map(cell => cell.trim())

const isRow = (line: string) => /^\s*\|.*\|\s*$/.test(line)
const isRule = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line)

export function narrowTables(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  let isCode = false
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ''
    if (/^\s*(```|~~~)/.test(line)) isCode = !isCode
    const next = lines[i + 1] ?? ''
    if (isCode || !isRow(line) || !isRule(next)) {
      out.push(line)
      continue
    }
    const head = cells(line)
    i += 1 // the rule line
    while (isRow(lines[i + 1] ?? '')) {
      i += 1
      const row = cells(lines[i] ?? '')
      const [first, ...rest] = row
      const pairs = rest
        .map((cell, j) => (cell ? `${head[j + 1] ? `${head[j + 1]}: ` : ''}${cell}` : ''))
        .filter(Boolean)
      out.push(`- ${[first, ...pairs].filter(Boolean).join(' · ')}`)
    }
  }
  return out.join('\n')
}
