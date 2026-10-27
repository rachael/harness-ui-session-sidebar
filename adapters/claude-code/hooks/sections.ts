// Optional extra sections (sections/ID.json), drawn by every harness. Data only: nothing runs.
export type Section = { title: string; icon: string; rows: { label: string; value: string }[] }

const clean = (value: unknown, limit = 200) => typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f-\x9f]/g, '').slice(0, limit) : ''

export function parseSections(text: string, now = Date.now()): Section[] {
  if (text.length > 65536) return []
  let data: any
  try { data = JSON.parse(text) } catch { return [] }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return []
  return Object.keys(data).sort().slice(0, 12).flatMap(id => {
    const section = data[id]
    if (!section || typeof section !== 'object' || section.visible === false || !clean(section.title, 60) || !Array.isArray(section.rows ?? [])) return []
    const rows = (section.rows ?? []).slice(0, 12).flatMap((row: any) => {
      if (!row || typeof row !== 'object') return []
      if (row.expiresAt !== undefined && (typeof row.expiresAt !== 'number' || row.expiresAt <= now)) return []
      const value = clean(row.value)
      return value ? [{ label: clean(row.label, 40), value }] : []
    })
    return [{ title: clean(section.title, 60), icon: clean(section.icon, 4), rows }]
  })
}
