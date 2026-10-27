import { expect, mock, test } from 'claude-code/testing'

import { EXPECTED, OVERVIEW, SECTIONS } from './fixtures'
import { PANE, ran } from './kit'

const ROOT = '/state/harness-ui'
const SESSION = 'fixture-session-1'
const RECORD = `${ROOT}/sessions/claude/${SESSION}.json`

function engine(on: any, files: Record<string, string>) {
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: ROOT })
  on('session.id', () => ({ value: SESSION }))
  on('fs.read', (_: unknown, e: { path: string }) => (e.path in files ? { value: files[e.path] } : { deny: 'no such file' }))
  on('process.run', () => ran(1))
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
}

const texts = async (ui: any): Promise<string[]> => (await ui.findAll({ type: 'Text' })).map((t: { text: string }) => t.text)

test('the shared fixture draws the same overview: verified surfaced content, resolved and unverified hidden', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, { [RECORD]: JSON.stringify(OVERVIEW), [`${ROOT}/sections/${SESSION}.json`]: JSON.stringify(SECTIONS) })
  await $.session.start({ cwd: '/home/example' } as never)
  await clock.advance(10)

  const ui = await $.ui.mount(PANE)
  const all = await texts(ui)
  for (const wanted of EXPECTED.visible as string[]) expect(all.some(text => text.includes(wanted))).toBe(true)
  for (const unwanted of EXPECTED.hidden as string[]) expect(all.some(text => text === unwanted || text.endsWith(` ${unwanted}`))).toBe(false)

  // Each surfaced item carries its harness badge.
  for (const [id, badge] of Object.entries(EXPECTED.badges as Record<string, string>)) {
    const entry = (OVERVIEW.surfaced as { id: string; kind: string; text: string }[]).find(item => item.id === id)!
    const shown = entry.kind === 'decision' ? `Decision: ${entry.text}` : entry.text
    expect(all).toContain(`${badge} ${shown}`)
  }

  // A captured ask the agent also surfaced shows once, under Asks.
  for (const [text, count] of Object.entries(EXPECTED.duplicates as Record<string, number>)) {
    expect(all.filter(line => line.endsWith(text)).length).toBe(count)
  }
  expect(all.some(text => /^Asks/.test(text))).toBe(true)
  expect(all.some(text => /^Notes/.test(text))).toBe(true)
})

test('surfaced content is read live from the canonical record, envelope or raw', async ($, on) => {
  const clock = mock.clock(on)
  const files: Record<string, string> = { [RECORD]: JSON.stringify({ schema_version: 1, revision: 'r1', data: { surfaced: [] } }) }
  engine(on, files)
  await $.session.start({ cwd: '/home/example' } as never)
  await clock.advance(10)
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /^Notes/ })).toBeUndefined()

  // Another process publishes a verified note; the next poll draws it with its badge.
  const note = { id: 'n1', kind: 'note', text: 'The lens is clean.', source_anchor: 'message:1', surfaced_by: 'codex', verified: true, status: 'open' }
  files[RECORD] = JSON.stringify({ schema_version: 1, revision: 'r2', data: { surfaced: [note] } })
  await clock.advance(2000)
  expect((await ui.find({ type: 'Text', text: /The lens is clean/ }))?.text).toBe('C The lens is clean.')

  // Resolved: it leaves.
  files[RECORD] = JSON.stringify({ surfaced: [{ ...note, status: 'resolved' }] })
  await clock.advance(2000)
  expect(await ui.find({ type: 'Text', text: /The lens is clean/ })).toBeUndefined()
})

test('badges come from the entry, then HARNESS_UI_BADGES, then the provider default', async ($, on) => {
  const clock = mock.clock(on)
  const entry = (id: string, by: string, badge?: unknown) => ({ id, kind: 'note', text: `note ${id}`, source_anchor: 'm', surfaced_by: by, verified: true, status: 'open', ...(badge === undefined ? {} : { badge }) })
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: ROOT, HARNESS_UI_BADGES: '{"gemini":"G+"}' })
  on('session.id', () => ({ value: SESSION }))
  const record = { surfaced: [entry('a', 'claude'), entry('b', 'gemini'), entry('c', 'other'), entry('d', 'codex', '★'), entry('e', 'codex', 'x\u0007y'), entry('f', 'claude', 'TOO-LONG-BADGE')] }
  on('fs.read', (_: unknown, e: { path: string }) => (e.path === RECORD ? { value: JSON.stringify(record) } : { deny: 'no such file' }))
  on('process.run', () => ran(1))
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/home/example' } as never)
  await clock.advance(10)
  const all = await texts(await $.ui.mount(PANE))
  expect(all).toContain('✳ note a')
  expect(all).toContain('G+ note b')
  expect(all).toContain('O note c')
  expect(all).toContain('★ note d')
  expect(all).toContain('C note e')
  expect(all).toContain('✳ note f')
})
