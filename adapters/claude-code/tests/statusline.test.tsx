import { expect, mock, test } from 'claude-code/testing'

import { addUsage } from '../hooks/statusline'
import { ran } from './kit'

const ROOT = '/state/harness-ui'
const FILE = `${ROOT}/staging/claude-status-1.usage.json`

function engine(on: any, startedAt: { value: number }) {
  const clock = mock.clock(on)
  const writes: { path: string; text: string }[] = []
  const statuses: unknown[] = []
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: ROOT })
  on('ui.status', (_: unknown, e: unknown) => {
    statuses.push(e)
    return { value: undefined }
  })
  on('fs.write', (_: unknown, e: { path: string; text: string }) => {
    writes.push(e)
    return { value: undefined }
  })
  on('session.id', () => ({ value: 'status-1' }))
  on('session.usage', () => ({ value: { startedAt: startedAt.value, context: { window: 200_000 }, rateLimits: [] } }))
  const runs: (readonly string[])[] = []
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push(e.argv)
    return ran(e.argv.includes('--merge') ? 0 : 1)
  })
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('session.measure', (_: unknown, e: { changed: string[] }) => ({ changed: e.changed }))
  return { clock, writes, statuses, runs }
}

const usageWrites = (writes: { path: string; text: string }[]) => writes.filter(w => w.path === FILE).map(w => JSON.parse(w.text).usage)

test('off by default: nothing collected, nothing written', async ($, on) => {
  const { writes } = engine(on, { value: 100 })
  await $.session.start({ cwd: '/home/example' } as never)
  expect(usageWrites(writes)).toEqual([])
})

test('switched on, it merges the session totals into the shared record and draws no row of its own', { options: { statusline: true } }, async ($, on) => {
  const { writes, statuses, runs } = engine(on, { value: 100 })
  await $.session.start({ cwd: '/home/example' } as never)
  expect(usageWrites(writes).at(-1)).toMatchObject({ schema: 1, startedAt: 100, input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })
  // merged into the session record through the shared publisher, never written there directly
  expect(runs.at(-1)).toEqual(['python3', expect.stringMatching(/\/runtime\/harness-ui-state\.py$/), 'state', 'publish-session', 'claude', 'status-1', '--input-file', FILE, '--merge', '--consume-input'])
  expect(writes.some(w => w.path.includes('/sessions/'))).toBe(false)
  expect(statuses).toEqual([])
})

test('a new session start (a /clear) restarts the totals', { options: { statusline: true } }, async ($, on) => {
  const startedAt = { value: 100 }
  const { writes } = engine(on, startedAt)
  await $.session.start({ cwd: '/home/example' } as never)
  startedAt.value = 900
  await $.session.measure({ context: { window: 200_000 }, rateLimits: [], changed: ['context'] } as never)
  expect(usageWrites(writes).at(-1)).toMatchObject({ startedAt: 900, input: 0 })
})

test('each request adds its usage to the totals (the request hook itself the kit cannot drive)', () => {
  const usage = { input_tokens: 1_000, output_tokens: 500, cache_read_input_tokens: 9_000, cache_creation_input_tokens: 20 }
  expect(addUsage(addUsage({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, usage), usage)).toEqual({ input: 2_000, output: 1_000, cacheRead: 18_000, cacheWrite: 40 })
})
