// Status line collector (contracts/statusline.md). Claude Code's own statusLine slot draws
// the line, through the harness-ui renderer; its payload lacks this session's request totals,
// so this collects them: each model request's uncached input, output and cache reads and
// writes, summed for the session and merged into the session record's `usage` field through
// the shared publisher. Nothing here draws, polls, prices or asks a model.
import { atom, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import { publishFields } from './state'

type On = Parameters<Register>[0]
type Tokens = { input: number; output: number; cacheRead: number; cacheWrite: number }
type Usage = { input_tokens: number; output_tokens: number; cache_read_input_tokens: number; cache_creation_input_tokens: number }

// This session's request totals, kept across reloads; `startedAt` says which session they
// count, so a /clear (a new start) begins them again; `since` is when this count began,
// later than the session's start after a resume or restart.
const totals = atom({ plugin: 'info-pane', key: 'sessionTokens' } as const, { startedAt: 0, since: 0, input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })

// One request's usage, as the API reports it, added to running totals.
export function addUsage<T extends Tokens>(t: T, usage: Usage): T {
  return {
    ...t,
    input: t.input + usage.input_tokens,
    output: t.output + usage.output_tokens,
    cacheRead: t.cacheRead + usage.cache_read_input_tokens,
    cacheWrite: t.cacheWrite + usage.cache_creation_input_tokens,
  }
}

// Usage of requests not yet written; parallel subagent requests add here and one write
// takes them all, so they never contend for the state.
const ZERO: Tokens = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }
let pending: Tokens = ZERO
let isFlushing = false

// Publications in order, one at a time: a later total never lands before an earlier one.
let saving = Promise.resolve()
// Requests come in bursts; their usage is published at most this often.
const PUBLISH_MS = 1500

function save($: Engine) {
  saving = saving.then(async () => {
    const t = await read($, totals)
    if (!t.startedAt) return
    await publishFields($, await $.session.id(), 'usage', { usage: { schema: 1, ...t, updated: await $.clock.now() } })
  }).catch(() => undefined)
  return saving
}

// The session's start, from the engine: a new one (a /clear, a new launch) restarts the count.
async function measure($: Engine) {
  const { startedAt } = await $.session.usage()
  if ((await read($, totals)).startedAt === startedAt) return false
  const since = await $.clock.now()
  await update($, totals, () => ({ startedAt, since, ...ZERO }))
  return true
}

async function flush($: Engine) {
  const adding = pending
  pending = ZERO
  isFlushing = false
  try {
    await measure($)
    await update($, totals, t => addUsage(t, { input_tokens: adding.input, output_tokens: adding.output, cache_read_input_tokens: adding.cacheRead, cache_creation_input_tokens: adding.cacheWrite }))
    await save($)
  } catch {
    // a lost write undercounts the session; the next request writes again
  }
}

// Run from the plugin's one session.start hook.
export async function startStatusline($: Engine) {
  await measure($)
  await save($)
}

export function registerStatusline(on: On) {
  // After each main turn: a /clear in between starts the count again.
  on('session.measure', async ($, e, next) => {
    if (await measure($).catch(() => false)) await save($).catch(() => undefined)

    return next(e)
  })

  // Each model request, subagents' included (they are this session's). The hook sits in the
  // path of every request, so nothing after the request may fail it: usage is gathered in a
  // pending sum and written once, after the request; any failure only undercounts.
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
    try {
      if (result.usage) {
        pending = addUsage(pending, result.usage)
        if (!isFlushing) {
          isFlushing = true
          $.clock.after(PUBLISH_MS, () => void flush($))
        }
      }
    } catch {
      // the count misses this request; the request itself is untouched
    }

    return result
  })
}
