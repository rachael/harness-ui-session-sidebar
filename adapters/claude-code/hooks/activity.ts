// Activity producer: whether this session is working, idle, or waiting on the person, from
// the engine's own events, merged into the session record's `activity` field for any viewer
// (the session sidebar, other harnesses' panes):
//   { status: 'working' | 'idle' | 'awaiting_input' | 'unknown', needs_input, updatedAt, source }
// A running process says nothing about work; only these events do, and before the first one
// the status is unknown.
import type { EngineInterface as Engine, Register } from 'claude-code'

import { publishFields } from './state'

type On = Parameters<Register>[0]
export type Status = 'working' | 'idle' | 'awaiting_input' | 'unknown'
export type Activity = { status: Status; needs_input: boolean }

// Notifications that mean the engine is waiting on the person to answer something now.
const ASKING = new Set(['permission_prompt', 'elicitation_dialog'])

// The next activity after an event; pure, so the semantics are tested without the engine.
export function next(now: Activity, event: 'prompt' | 'tool-done' | 'ask-open' | 'ask-closed' | 'stop' | 'notify', kind = ''): Activity {
  switch (event) {
    case 'prompt':
      return { status: 'working', needs_input: false }
    case 'tool-done':
    case 'ask-closed':
      return { status: 'working', needs_input: false }
    case 'ask-open':
      return { status: 'awaiting_input', needs_input: true }
    case 'stop':
      // a turn that ended while something still waits on the person keeps saying so
      return { status: 'idle', needs_input: now.needs_input }
    case 'notify':
      if (ASKING.has(kind)) return { status: 'awaiting_input', needs_input: true }
      if (kind === 'idle_prompt') return { status: 'idle', needs_input: now.needs_input }
      return now
  }
}

// What the events say now, and what the shared record last took. They differ while a
// publication is pending or failed; a failed one is retried, with growing waits, a few times,
// and the next event tries again in any case, so a final idle or needs_input is not lost to
// a busy session lock.
let current: Activity = { status: 'unknown', needs_input: false }
let published = ''
let publishing = Promise.resolve()
let retries = 0
const RETRY_MS = [500, 2000, 8000]

const keyOf = (a: Activity) => `${a.status}:${a.needs_input}`

function sync($: Engine) {
  publishing = publishing
    .then(async () => {
      const wanted = current
      if (keyOf(wanted) === published) return
      const ok = await publishFields($, await $.session.id(), 'activity', { activity: { ...wanted, updatedAt: await $.clock.now(), source: 'native-event' } }).catch(() => false)
      if (ok) {
        published = keyOf(wanted)
        retries = 0
        if (keyOf(current) !== published) sync($)
      } else if (retries < RETRY_MS.length) {
        $.clock.after(RETRY_MS[retries++]!, () => void sync($))
      }
    })
    .catch(() => undefined)
  return publishing
}

// Take the activity an event leads to, and publish it unless the record already has it.
function move($: Engine, change: Activity) {
  current = change
  retries = 0
  return sync($)
}

// These hooks only watch; a failure goes on as if they were not here.
function passThrough($: Engine, e: any, next: (e: any) => any) {
  return next(e)
}

export function registerActivity(on: On) {
  on('classic.UserPromptSubmit', async ($, e, nextHook) => {
    void move($, next(current, 'prompt'))

    return nextHook(e)
  }).catch(passThrough)

  on('classic.PostToolUse', async ($, e, nextHook) => {
    if (current.status === 'awaiting_input') void move($, next(current, 'tool-done'))

    return nextHook(e)
  }).catch(passThrough)

  on('classic.Stop', async ($, e, nextHook) => {
    void move($, next(current, 'stop'))

    return nextHook(e)
  }).catch(passThrough)

  // A turn that ends on an API error (retries exhausted) ends like any other.
  on('classic.StopFailure', async ($, e, nextHook) => {
    void move($, next(current, 'stop'))

    return nextHook(e)
  }).catch(passThrough)

  // A permission dialog is the session waiting on the person.
  on('classic.PermissionRequest', async ($, e, nextHook) => {
    if (!(e as { agent_id?: string }).agent_id) void move($, next(current, 'ask-open'))

    return nextHook(e)
  }).catch(passThrough)

  on('classic.Notification', async ($, e, nextHook) => {
    void move($, next(current, 'notify', (e as { notification_type?: string }).notification_type ?? ''))

    return nextHook(e)
  }).catch(passThrough)

  // A question dialog is the session waiting on the person until it is answered.
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, nextHook) => {
    if (e.agentId === undefined) void move($, next(current, 'ask-open'))
    try {
      return await nextHook(e)
    } finally {
      if (e.agentId === undefined) void move($, next(current, 'ask-closed'))
    }
  }).catch(passThrough)
}
