// Where a waiting ask goes when its description is clicked: into the empty line the cursor
// is on, else the next empty line below it, else a new last line. What was typed is kept;
// the caret lands at the end of what was placed.

export function placeInDraft(draft: string, cursor: number, entry: string): { text: string; cursor: number } {
  const lines = draft.split('\n')
  let at = 0
  for (let start = 0; at < lines.length - 1 && start + lines[at]!.length < cursor; at++) start += lines[at]!.length + 1
  let target = lines.findIndex((line, i) => i >= at && !line.trim())
  if (target === -1) {
    lines.push(entry)
    target = lines.length - 1
  } else {
    lines[target] = entry
  }
  return { text: lines.join('\n'), cursor: lines.slice(0, target + 1).join('\n').length }
}

// One line: the ask's title, then its description.
export const askEntry = (title: string, detail: string) => (detail ? `${title} — ${detail}` : title)
