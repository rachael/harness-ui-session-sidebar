import { expect, mock, test } from 'claude-code/testing'

import { age, glyph, parseSessions, words } from '../hooks/session-sidebar'
import { ran } from './kit'

const SIDEBAR = {
  plugin: 'info-pane',
  surface: 'terminal',
  component: 'Pane',
  requestId: 'session-sidebar',
  props: { title: 'Sessions', isFocused: false, bodyColumns: 32, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
  viewport: { columns: 200, rows: 50, isFullscreen: true },
} as const

const LIST = (NOW: number) => ({
  schema: 1,
  sessions: [
    { provider: 'claude', badge: '✳', session: 'side-1', state: 'running', title: 'Lighthouse refactor', cwd: '/home/example/lighthouse', branch: 'main', model: '', updated_at: NOW / 1000 - 30, activity: { status: 'working', needs_input: false, updated_at: NOW / 1000 - 30 }, terminal: { kind: 'iterm2', focusable: true } },
    { provider: 'codex', badge: 'C', session: 'fixture-b', state: 'running', title: '', cwd: '/home/example/harbor', branch: '', model: '', updated_at: NOW / 1000 - 600, activity: { status: 'idle', needs_input: true, updated_at: NOW / 1000 - 600 }, terminal: { kind: 'iterm2', focusable: true } },
    { provider: 'codex', badge: 'C', session: 'fixture-c', state: 'unknown', title: 'Fog horn', cwd: '', branch: '', model: '', updated_at: NOW / 1000 - 7200, terminal: { kind: 'none', focusable: false } },
  ],
})

function engine(on: any, focus = { status: 'focused', detail: '' }) {
  const runs: (readonly string[])[] = []
  const clock = mock.clock(on)
  mock.env(on, { HOME: '/home/example' })
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('session.end', () => ({ sessionId: 'side-1' }))
  on('command.register', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.id', () => ({ value: 'side-1' }))
  // The plugin's bundled runtime runs the commands: python3 on runtime/harness-ui-state.py.
  // `sessions()` gives each call from 'sessions' on, after checking that executable.
  const tail = (argv: readonly string[]) => argv.slice(argv.indexOf('sessions'))
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push(e.argv)
    const args = tail(e.argv)
    if (args[1] === 'list') return ran(0, JSON.stringify(LIST(clock.now())))
    if (args[1] === 'focus') return ran(focus.status === 'focused' ? 0 : 3, JSON.stringify(focus))
    return ran(args[0] === 'sessions' ? 0 : 1, '{}')
  })
  const isBundled = (argv: readonly string[]) => argv[0] === 'python3' && /\/runtime\/harness-ui-state\.py$/.test(argv[1] ?? '') && argv[2] === 'sessions'
  return {
    runs,
    clock,
    sessions: () => runs.filter(argv => argv.includes('sessions')).map(argv => {
      if (!isBundled(argv)) throw new Error(`not the bundled runtime: ${argv.join(' ')}`)
      return ['harness-ui', ...tail(argv)]
    }),
  }
}

test('off by default: no announce, no sidebar', async ($, on) => {
  const { sessions } = engine(on)
  await $.session.start({ cwd: '/home/example' } as never)
  expect(sessions()).toEqual([])
})

test('switched on, the session announces itself and the sidebar draws one line per session, grouped by harness', { options: { sessionSidebar: true } }, async ($, on) => {
  const { sessions } = engine(on)
  await $.session.start({ cwd: '/home/example' } as never)
  expect(sessions()[0]).toEqual(['harness-ui', 'sessions', 'announce', 'claude', 'side-1'])
  expect(sessions()[1]).toEqual(['harness-ui', 'sessions', 'list'])
  const ui = await $.ui.mount(SIDEBAR)
  expect(await ui.find({ type: 'Text', text: '✳ Claude Code' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'C Codex' })).toBeDefined()
  // 32 columns leave the title 18 cells beside the toggle, hotkey, mark, › and age: clipped
  expect((await ui.find({ type: 'Button', key: 'sidebar:row:claude:side-1' }))?.text).toBe('● › Lighthouse refact…')
  // no title: the folder, home-relative; never a title of its own making. Idle, but an ask waits on the person.
  expect((await ui.find({ type: 'Button', key: 'sidebar:row:codex:fixture-b' }))?.text).toBe('? ~/harbor')
  // no activity recorded: unknown, never inferred from the process
  expect((await ui.find({ type: 'Button', key: 'sidebar:row:codex:fixture-c' }))?.text).toBe('· Fog horn')
  // minimal: no details until their own control is pressed
  expect(await ui.find({ type: 'Text', text: 'idle, waiting on you' })).toBeUndefined()
  await ui.press({ key: 'sidebar:toggle:codex:fixture-b' })
  expect(await ui.find({ type: 'Text', text: 'idle, waiting on you' })).toBeDefined()
  expect((await ui.find({ type: 'Button', key: 'sidebar:toggle:codex:fixture-b' }))?.text).toBe('[-]')
})

test('in a narrow dock each session stays on one line: the label is clipped, the age kept', { options: { sessionSidebar: true } }, async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/home/example' } as never)
  // 28 columns, as the dock gives a tab beside the info pane
  const ui = await $.ui.mount({ ...SIDEBAR, props: { ...SIDEBAR.props, bodyColumns: 28 } })
  // toggle [+] 3, hotkey "1: " 3, mark and space 2, own-session › 2, age " now" 4: 14 left
  expect((await ui.find({ type: 'Button', key: 'sidebar:row:claude:side-1' }))?.text).toBe('● › Lighthouse re…')
  // the folder fits as it is
  expect((await ui.find({ type: 'Button', key: 'sidebar:row:codex:fixture-b' }))?.text).toBe('? ~/harbor')
  for (const row of (await ui.findAll({ type: 'Button' })).filter((b: { key?: string }) => b.key?.startsWith('sidebar:row:'))) {
    expect(3 + 3 + [...(row.text ?? '')].length + 4 <= 28).toBe(true)
  }
})

test('a press switches through the CLI with the options; a refusal is said and nothing else is tried', { options: { sessionSidebar: true, switchPanes: false } }, async ($, on) => {
  const { sessions } = engine(on, { status: 'other-window', detail: 'the session is in another iTerm2 window' })
  await $.session.start({ cwd: '/home/example' } as never)
  const ui = await $.ui.mount(SIDEBAR)
  const before = sessions().length
  await ui.press({ key: 'sidebar:row:codex:fixture-b' })
  const after = sessions().slice(before)
  expect(after).toEqual([['harness-ui', 'sessions', 'focus', 'codex', 'fixture-b', '--switch-tabs', '--no-switch-panes']])
  expect(await ui.find({ type: 'Text', text: 'It is in another window; nothing switched.' })).toBeDefined()
})

test('a harness switched off is left out of the list', { options: { sessionSidebar: true, showCodex: false } }, async ($, on) => {
  const { sessions } = engine(on)
  await $.session.start({ cwd: '/home/example' } as never)
  expect(sessions()[1]).toEqual(['harness-ui', 'sessions', 'list', '--hide', 'codex'])
})

test('each activity has one glyph and its words; needs_input comes first', () => {
  const cases = [
    [{ status: 'working', needsInput: false }, '●', 'working'],
    [{ status: 'idle', needsInput: false }, '○', 'idle'],
    [{ status: 'awaiting_input', needsInput: true }, '?', 'waiting on you'],
    [{ status: 'working', needsInput: true }, '?', 'working, waiting on you'],
    [{ status: 'unknown', needsInput: false }, '·', 'activity unknown'],
  ] as const
  for (const [activity, mark, said] of cases) expect([glyph(activity), words(activity)]).toEqual([mark, said])
  expect(parseSessions({ sessions: [{ provider: 'claude', session: 'x', activity: { status: 'busy', needs_input: 'yes' } }] })[0]!.activity).toEqual({ status: 'unknown', needsInput: false })
})

test('the session forgets itself at its end', { options: { sessionSidebar: true } }, async ($, on) => {
  const { sessions } = engine(on)
  await $.session.start({ cwd: '/home/example' } as never)
  await $.session.end({ reason: 'exit' } as never)
  expect(sessions().at(-1)).toEqual(['harness-ui', 'sessions', 'forget', 'claude', 'side-1'])
})

test('parsing keeps only well-formed rows; ages read as now, minutes, hours, days', () => {
  expect(parseSessions({ sessions: [{ provider: 'claude' }, null, ...LIST(1_772_442_600_000).sessions] }).map(row => row.session)).toEqual(['side-1', 'fixture-b', 'fixture-c'])
  expect(parseSessions('nonsense')).toEqual([])
  expect([age(5), age(600), age(7200), age(200_000)]).toEqual(['now', '10m', '2h', '2d'])
})
