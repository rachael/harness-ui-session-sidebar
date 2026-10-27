import { expect, mock, test } from 'claude-code/testing'

import { next } from '../hooks/activity'
import type { Activity } from '../hooks/activity'
import { ran } from './kit'

const UNKNOWN: Activity = { status: 'unknown', needs_input: false }
const FILE = '/state/harness-ui/staging/claude-act-1.activity.json'

test('transitions come from events only: a prompt works, a dialog waits, a stop keeps what still waits', () => {
  const working = next(UNKNOWN, 'prompt')
  expect(working).toEqual({ status: 'working', needs_input: false })
  const asking = next(working, 'ask-open')
  expect(asking).toEqual({ status: 'awaiting_input', needs_input: true })
  expect(next(asking, 'stop')).toEqual({ status: 'idle', needs_input: true })
  expect(next(asking, 'ask-closed')).toEqual({ status: 'working', needs_input: false })
  expect(next(working, 'notify', 'permission_prompt')).toEqual({ status: 'awaiting_input', needs_input: true })
  expect(next(working, 'notify', 'idle_prompt')).toEqual({ status: 'idle', needs_input: false })
  expect(next(working, 'notify', 'auth_success')).toEqual(working)
})

test('each change is merged into the session record through the shared publisher, in order, once (events that outpace it publish only the latest)', async ($, on) => {
  const clock = mock.clock(on)
  const writes: { path: string; text: string }[] = []
  const runs: (readonly string[])[] = []
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: '/state/harness-ui' })
  on('session.id', () => ({ value: 'act-1' }))
  on('fs.write', (_: unknown, e: { path: string; text: string }) => {
    writes.push(e)
    return { value: undefined }
  })
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push(e.argv)
    return ran(0)
  })
  for (const event of ['UserPromptSubmit', 'Stop', 'StopFailure', 'Notification', 'PermissionRequest'] as const) on(`classic.${event}`, (_: unknown, e: unknown) => e as never)

  await $.classic.UserPromptSubmit({ prompt: 'Polish the lens.' } as never)
  await clock.settle()
  await $.classic.UserPromptSubmit({ prompt: 'And the brass.' } as never)
  await clock.settle()
  await $.classic.Notification({ message: 'Claude needs your permission', notification_type: 'permission_prompt' } as never)
  await clock.settle()
  await $.classic.Stop({ stop_hook_active: false } as never)
  await clock.settle()
  await $.classic.UserPromptSubmit({ prompt: 'Once more.' } as never)
  await clock.settle()
  await $.classic.PermissionRequest({ tool_name: 'Bash', tool_input: {} } as never)
  await clock.settle()
  await $.classic.StopFailure({ error: 'rate_limit' } as never)
  await clock.settle()
  const published = writes.filter(w => w.path === FILE).map(w => JSON.parse(w.text).activity)
  expect(published.map((a: { status: string; needs_input: boolean }) => [a.status, a.needs_input])).toEqual([
    ['working', false],
    ['awaiting_input', true],
    ['idle', true],
    ['working', false],
    ['awaiting_input', true],
    ['idle', true],
  ])
  expect(published.every((a: { source: string }) => a.source === 'native-event')).toBe(true)
  const publishes = runs.filter(argv => argv.includes('publish-session'))
  expect(publishes.length).toBe(6)
  expect(publishes.every(argv => argv.includes('--merge'))).toBe(true)
})

test('a failed publication is retried and never taken for done; later changes still go out', async ($, on) => {
  const clock = mock.clock(on)
  const published: string[] = []
  let failures = 2
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: '/state/harness-ui', HARNESS_UI_COMMAND: 'harness-ui' })
  on('session.id', () => ({ value: 'act-2' }))
  let staged = ''
  on('fs.write', (_: unknown, e: { path: string; text: string }) => {
    if (e.path.endsWith('.activity.json')) staged = e.text
    return { value: undefined }
  })
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    if (!e.argv.includes('publish-session')) return ran(1)
    if (failures > 0) {
      failures--
      return ran(2) // the session lock was busy
    }
    const a = JSON.parse(staged).activity
    published.push(`${a.status}:${a.needs_input}`)
    return ran(0)
  })
  for (const event of ['UserPromptSubmit', 'Stop', 'Notification'] as const) on(`classic.${event}`, (_: unknown, e: unknown) => e as never)

  await $.classic.UserPromptSubmit({ prompt: 'Polish the lens.' } as never)
  await $.classic.Notification({ message: 'Claude needs your permission', notification_type: 'permission_prompt' } as never)
  await clock.settle()
  // both attempts failed: nothing recorded as published yet
  expect(published).toEqual([])
  await clock.advance(500)
  await clock.settle()
  // the retry publishes the latest state, not a stale one
  expect(published).toEqual(['awaiting_input:true'])
  await $.classic.Stop({ stop_hook_active: false } as never)
  await clock.settle()
  expect(published).toEqual(['awaiting_input:true', 'idle:true'])
})

test('with every feature off, nothing is published unless activity is asked for', { options: { infoPane: false, contextDrawer: false, statusline: false } }, async ($, on) => {
  const clock = mock.clock(on)
  const runs: (readonly string[])[] = []
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: '/state/harness-ui' })
  on('session.id', () => ({ value: 'act-3' }))
  on('fs.write', () => ({ value: undefined }))
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push(e.argv)
    return ran(0)
  })
  on('classic.UserPromptSubmit', (_: unknown, e: unknown) => e as never)
  await $.classic.UserPromptSubmit({ prompt: 'Polish the lens.' } as never)
  await clock.settle()
  expect(runs.filter(argv => argv.includes('publish-session'))).toEqual([])
})

test('activity: on publishes even with every feature off', { options: { infoPane: false, contextDrawer: false, statusline: false, activity: 'on' } }, async ($, on) => {
  const clock = mock.clock(on)
  const runs: (readonly string[])[] = []
  mock.env(on, { HOME: '/home/example', HARNESS_UI_STATE_DIR: '/state/harness-ui' })
  on('session.id', () => ({ value: 'act-4' }))
  on('fs.write', () => ({ value: undefined }))
  on('process.run', (_: unknown, e: { argv: readonly string[] }) => {
    runs.push(e.argv)
    return ran(0)
  })
  on('classic.UserPromptSubmit', (_: unknown, e: unknown) => e as never)
  await $.classic.UserPromptSubmit({ prompt: 'Polish the lens.' } as never)
  await clock.settle()
  expect(runs.filter(argv => argv.includes('publish-session')).length).toBe(1)
})
