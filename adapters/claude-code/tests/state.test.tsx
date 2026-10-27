import { expect, mock, test } from 'claude-code/testing'

import { PANE, ran } from './kit'

type Run = { argv: readonly string[] }

function engine(on: any, env: Record<string, string>, options: { exitCode?: number; files?: Record<string, string> } = {}) {
  const runs: Run[] = []
  const writes: { path: string; text: string }[] = []
  mock.env(on, { HOME: '/home/example', ...env })
  on('session.id', () => ({ value: 'state-1' }))
  on('fs.read', (_: unknown, e: { path: string }) => (options.files && e.path in options.files ? { value: options.files[e.path] } : { deny: 'no such file' }))
  on('fs.write', (_: unknown, e: { path: string; text: string }) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('process.run', (_: unknown, e: Run) => {
    runs.push(e)
    const isPublish = e.argv.includes('publish-session')
    return ran(isPublish ? (options.exitCode ?? 0) : 1)
  })
  on('tool.call', { tool: 'TaskCreate' }, (_: unknown, e: { subject: string }) => ({ result: { task: { id: '1', subject: e.subject } } }))
  on('tool.call', { tool: 'TaskUpdate' }, () => ({ result: { success: true, taskId: '1', updatedFields: ['status'] } }))
  const publishes = () => runs.filter(run => run.argv.includes('publish-session'))
  return { runs, writes, publishes }
}

test('publication goes through the harness-ui CLI with the shared argv, staged under the state root', async ($, on) => {
  const clock = mock.clock(on)
  const { writes, publishes } = engine(on, { HARNESS_UI_STATE_DIR: '/state/harness', CODEX_UI_STATE_DIR: '/state/codex' })
  await $.tool.call({ tool: 'TaskCreate', subject: 'Polish the lens', description: '' })
  await clock.advance(10)
  expect(publishes()[0]?.argv).toEqual([
    'python3', expect.stringMatching(/\/runtime\/harness-ui-state\.py$/), 'state', 'publish-session', 'claude', 'state-1',
    '--input-file', '/state/harness/staging/claude-state-1.json',
    '--retain-field', 'surfaced', '--retain-field', 'usage', '--retain-field', 'terminal', '--retain-field', 'activity', '--consume-input',
  ])
  // the plugin writes only its staging file, never the canonical record
  expect(writes.map(w => w.path)).toEqual(['/state/harness/staging/claude-state-1.json'])
  const staged = JSON.parse(writes[0]!.text)
  expect(staged.harness).toBe('Claude Code')
  expect(staged.session).toBe('state-1')
  expect(staged.views.main.tasks[0].subject).toBe('Polish the lens')
  expect('surfaced' in staged).toBe(false)
  expect('iterm' in staged).toBe(false)
})

test('HARNESS_UI_COMMAND overrides the executable; CODEX_UI_STATE_DIR and XDG_STATE_HOME are fallbacks', async ($, on) => {
  const clock = mock.clock(on)
  const { publishes } = engine(on, { HARNESS_UI_COMMAND: '/opt/tools/harness-ui', CODEX_UI_STATE_DIR: '~/codex-state' })
  await $.tool.call({ tool: 'TaskCreate', subject: 'Polish the lens', description: '' })
  await clock.advance(10)
  expect(publishes()[0]?.argv[0]).toBe('/opt/tools/harness-ui')
  expect(publishes()[0]?.argv[6]).toBe('/home/example/codex-state/staging/claude-state-1.json')
})

test('the default state root is $XDG_STATE_HOME/harness-ui-mods, else ~/.local/state/harness-ui-mods', async ($, on) => {
  const clock = mock.clock(on)
  const { publishes } = engine(on, { XDG_STATE_HOME: '/xdg' })
  await $.tool.call({ tool: 'TaskCreate', subject: 'Polish the lens', description: '' })
  await clock.advance(10)
  expect(publishes()[0]?.argv[7]).toBe('/xdg/harness-ui-mods/staging/claude-state-1.json')
})

test('unchanged content is not published again: no timestamp-only revisions', async ($, on) => {
  const clock = mock.clock(on)
  const { publishes } = engine(on, {})
  await $.tool.call({ tool: 'TaskCreate', subject: 'Same task', description: '' })
  await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'in_progress' })
  await clock.advance(10)
  const count = publishes().length
  expect(count).toBeGreaterThan(0)
  await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'in_progress' })
  await clock.advance(100)
  expect(publishes().length).toBe(count)
})

test('a failing CLI leaves one staging file per session, never a raw canonical write, and the pane keeps working', async ($, on) => {
  const clock = mock.clock(on)
  const { writes, publishes } = engine(on, {}, { exitCode: 2 })
  await $.tool.call({ tool: 'TaskCreate', subject: 'First', description: '' })
  await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'in_progress' })
  await $.tool.call({ tool: 'TaskUpdate', taskId: '1', status: 'completed' })
  await clock.advance(10)
  expect(publishes().length).toBeGreaterThan(1)
  expect(new Set(writes.map(w => w.path))).toEqual(new Set(['/home/example/.local/state/harness-ui-mods/staging/claude-state-1.json']))
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /First/ })).toBeDefined()
})

test('a new process restores the pane from the canonical record, envelope or raw', async ($, on) => {
  const clock = mock.clock(on)
  const saved = {
    title: 'Lens cleaning',
    recap: 'Cleaning the lens before dusk.',
    pins: [{ id: 'm9', text: 'Use the soft cloth.', at: 1 }],
    views: { main: { answer: 'Done.', answerAt: 1, asks: [], tasks: [{ id: '1', subject: 'Wipe the lens', status: 'completed', from: 'task' }] } },
  }
  const files = { '/state/x/sessions/claude/state-1.json': JSON.stringify({ schema_version: 1, revision: 'r1', data: saved }) }
  const { publishes } = engine(on, { HARNESS_UI_STATE_DIR: '/state/x' }, { files })
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/home/example' } as never)
  await clock.advance(10)
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Lens cleaning' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Cleaning the lens before dusk.' })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: /^Done/ }))?.text).toBe('Done 1')
  expect(await ui.find({ type: 'Text', text: 'Use the soft cloth.' })).toBeDefined()
  // restoring is no change: nothing is published back
  expect(publishes()).toEqual([])
})

test('the pane inherits its background and runs only git and its bundled state runtime', async ($, on) => {
  const clock = mock.clock(on)
  const { runs } = engine(on, { ITERM_SESSION_ID: `w0t0p0:${['0A1B2C3D', '0000', '4000', '8000', '00000000000A'].join('-')}` })
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  await $.session.start({ cwd: '/home/example' } as never)
  await $.tool.call({ tool: 'TaskCreate', subject: 'Polish the lens', description: '' })
  await clock.advance(5000)
  expect(new Set(runs.map(run => run.argv[0]))).toEqual(new Set(['git', 'python3']))
  expect(runs.filter(run => run.argv[0] === 'python3').every(run => /\/runtime\/harness-ui-state\.py$/.test(run.argv[1] ?? ''))).toBe(true)
})

test('the state paths command decides the state root and badges (here an installed harness-ui)', async ($, on) => {
  const clock = mock.clock(on)
  const runs: string[][] = []
  const writes: string[] = []
  mock.env(on, { HOME: '/home/example', HARNESS_UI_COMMAND: 'harness-ui' })
  on('session.id', () => ({ value: 'state-1' }))
  on('fs.write', (_: unknown, e: { path: string }) => {
    writes.push(e.path)
    return { value: undefined }
  })
  const record = { surfaced: [{ id: 'n', kind: 'note', text: 'From a data module root.', source_anchor: 'm', surfaced_by: 'codex', verified: true, status: 'open' }] }
  on('fs.read', (_: unknown, e: { path: string }) => (e.path === '/modules/ui/sessions/claude/state-1.json' ? { value: JSON.stringify(record) } : { deny: 'no such file' }))
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push([...e.argv])
    if (e.argv[1] === 'state' && e.argv[2] === 'paths') {
      return ran(0, JSON.stringify({ provider: 'claude', state_root: '/modules/ui', publisher_command: null, environment: { HARNESS_UI_BADGES: '{"codex":"Cx"}' } }))
    }
    return ran(e.argv.includes('publish-session') ? 0 : 1)
  })
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('tool.call', { tool: 'TaskCreate' }, (_: unknown, e: { subject: string }) => ({ result: { task: { id: '1', subject: e.subject } } }))
  await $.session.start({ cwd: '/home/example' } as never)
  await $.tool.call({ tool: 'TaskCreate', subject: 'Polish the lens', description: '' })
  await clock.advance(10)
  expect(runs).toContainEqual(['harness-ui', 'state', 'paths', '--provider', 'claude'])
  expect(writes).toEqual(['/modules/ui/staging/claude-state-1.json'])
  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Cx From a data module root.' })).toBeDefined()
  // resolved once, not on every poll
  await clock.advance(10_000)
  expect(runs.filter(argv => argv[2] === 'paths').length).toBe(1)
})
