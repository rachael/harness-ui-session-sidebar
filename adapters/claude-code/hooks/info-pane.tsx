// Info pane: a vertical pane docked beside the transcript, drawn for the conversation in
// view (the main one, or the subagent opened from the tasks list).
//
// Two tabs. Overview, top to bottom: the conversation's title, harness line and recap with a
// little Claude on the left; then Asks and Notes surfaced by an agent, extra sections, Pinned,
// Last answer, Waiting on, In progress, Not started, Done. A section shows only when there is
// data for it. Responses: one row per reply; [+] shows a short summary, the row jumps to it.
//
// Every reply in the transcript gets a pin glyph beside it; a pinned reply shows in the pane,
// and a click there unpins it. Each change is published to the shared state root through the
// harness-ui CLI, so other harnesses' viewers can draw the same session (contracts/overview.md).
import { atom, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import type { Ask, External, Pin, Response, Task, View } from '../types'
import { CLAUDES } from './claudes'
import { askEntry, placeInDraft } from './draft'
import { narrowTables } from './narrow'
import { clipRow, remember, wrapRows } from './responses'
import { parseSections } from './sections'
import { contentKey, PROVIDER, publish, readRecord, SAFE_ID, sectionsFile, sessionFile, settings, stateRoot } from './state'
import { openSurfaced, parseBadges, sameText } from './surfaced'

type On = Parameters<Register>[0]

export const PANE = 'info-pane'
// The model's own way to keep Waiting on current: list the open asks, close the ones the
// person answered, set aside or made moot. Behind ToolSearch, so it costs no prompt space.
const ASKS_TOOL = 'mcp__info-pane__asks'
const MAIN = 'main'
const WIDTH = 44
const EMPTY: View = { answer: '', answerAt: 0, asks: [], tasks: [] }
const POLL_MS = 2000

const views = atom({ plugin: 'info-pane', key: 'views' } as const, {})
const harness = atom({ plugin: 'info-pane', key: 'harness' } as const, { model: '', cwd: '', branch: '' })
const isAnswerOpen = atom({ plugin: 'info-pane', key: 'isAnswerOpen' } as const, false)
const pins = atom({ plugin: 'info-pane', key: 'pins' } as const, [])
const meta = atom({ plugin: 'info-pane', key: 'meta' } as const, { title: '', recap: '', sessionTitle: '' })
const openAsks = atom({ plugin: 'info-pane', key: 'openAsks' } as const, [])
const corner = atom({ plugin: 'info-pane', key: 'corner' } as const, { name: '', frame: 0 })
const lastPrompt = atom({ plugin: 'info-pane', key: 'lastPrompt' } as const, '')
// Whether the transcript has filled the screen: true from the first reply drawn off screen.
// Last answer shows once there is one and this is true.
const screenFilled = atom({ plugin: 'info-pane', key: 'screenFilled' } as const, false)
const tab = atom({ plugin: 'info-pane', key: 'tab' } as const, 'overview')
const responses = atom({ plugin: 'info-pane', key: 'responses' } as const, [])
const openResponses = atom({ plugin: 'info-pane', key: 'openResponses' } as const, [])
const external = atom({ plugin: 'info-pane', key: 'external' } as const, { surfaced: [], sections: [] })
// The little Claude's speech row: what its last click said, while it shows; and whether the
// keyboard focus is on it, which shows the click hint as a hover does.
const said = atom({ plugin: 'info-pane', key: 'said' } as const, '')
const mascotFocus = atom({ plugin: 'info-pane', key: 'mascotFocus' } as const, false)
const MASCOT = 'little-claude'
const SPRITE_HOVER = { scope: MASCOT, color: 'suggestion', bold: true } as const
const SAID_MS = 4000
// What a click on each kind of Claude does, as the hint says it.
const HINTS = { toast: 'click: say hi', recap: 'click: recap', next: 'click: another Claude', animate: 'click: wiggle' } as const
let isFilledKnown = false
// Ask ids stay unique within a process even when two are minted in the same millisecond.
let askSeq = 0
// Which transcript the pane last drew for: replies drawn while a subagent is in view are not
// the main conversation's responses.
let viewing = MAIN

// The main window's spinner: Claude's spark cycling, out and back. The In progress header and
// rows use it, so the pane moves in step with the work. It ticks only while a task in the main
// conversation is in progress.
const SPARK = ['·', '✢', '✳', '✶', '✻', '✽', '✻', '✶', '✳', '✢']
const spin = atom({ plugin: 'info-pane', key: 'spin' } as const, 0)
let ticker: { cancel: () => void } | undefined

function syncSpinner($: Engine, view?: View) {
  void (async () => {
    const main = view ?? (await read($, views))[MAIN] ?? EMPTY
    const isBusy = main.tasks.some(t => t.status === 'in_progress')
    if (isBusy && !ticker) ticker = $.clock.every(150, () => void update($, spin, n => (n + 1) % SPARK.length))
    if (!isBusy && ticker) {
      ticker.cancel()
      ticker = undefined
    }
  })().catch(() => undefined)
}

const keyOf = (agentId?: string) => agentId ?? MAIN

async function edit($: Engine, key: string, change: (view: View) => View) {
  const all = await update($, views, all => ({ ...all, [key]: change(all[key] ?? EMPTY) }))
  void mirror($)
  if (key === MAIN) syncSpinner($, all[key])
}

// Publication: the record goes to the shared state root through the harness-ui CLI, and only
// when its content changed. A failed publication keeps the pane working from memory.
let publications = Promise.resolve()
let lastPublishedContent: string | undefined
let sourceTranscript: string | null = null

async function mirror($: Engine) {
  try {
    const session = await $.session.id()
    const where = await read($, harness)
    const { title, recap } = await read($, meta)
    const content = {
      harness: 'Claude Code',
      session,
      ...where,
      title,
      recap,
      pins: await read($, pins),
      views: await read($, views),
      source: { harness: PROVIDER, session, transcript: sourceTranscript },
    }
    publications = publications.catch(() => undefined).then(async () => {
      const serialized = contentKey(content)
      if (serialized === lastPublishedContent) return
      if (await publish($, session, { ...content, updated: await $.clock.now() })) lastPublishedContent = serialized
    })
    await publications
  } catch {
    // No CLI, no state root, or a refused publication: the live pane is unaffected.
  }
}

// Pane state lives in memory and starts empty in a new process (a resume, a restart). The
// published record outlives it, so a session that finds its own record on start picks up its
// views, title, recap and pins. Anything already in memory wins.
async function restore($: Engine) {
  try {
    const record = await readRecord($, sessionFile(await stateRoot($), await $.session.id()))
    if (!record) return
    const { updated: _updated, surfaced: _s, usage: _u, terminal: _t, activity: _a, ...content } = record
    lastPublishedContent = contentKey(content)
    const saved = record as { views?: Record<string, View>; title?: string; recap?: string; pins?: Pin[] }
    if (saved.views && typeof saved.views === 'object') {
      await update($, views, now => ({ ...saved.views, ...now }))
    }
    if (typeof saved.title === 'string' || typeof saved.recap === 'string') {
      await update($, meta, m => ({ ...m, title: m.title || clip(saved.title, 60), recap: m.recap || clip(saved.recap, 400) }))
    }
    if (Array.isArray(saved.pins)) await update($, pins, now => (now.length ? now : saved.pins!.filter(p => p && typeof p.id === 'string' && typeof p.text === 'string')))
  } catch {
    // no record yet, or one that doesn't parse: start empty
  }
}

// What other processes wrote for this session (surfaced content, extra sections), read from
// the canonical files. The atom changes only when the content does, so polling redraws nothing
// while nothing changed.
let lastExternal = ''
async function refreshExternal($: Engine) {
  const session = await $.session.id().catch(() => '')
  if (!SAFE_ID.test(session)) return
  const { root, badges: configured } = await settings($)
  const badges = parseBadges(configured)
  const surfaced = openSurfaced(await readRecord($, sessionFile(root, session)), badges)
  const sections = await $.fs
    .read(sectionsFile(root, session))
    .then(text => parseSections(JSON.stringify(unwrapText(text))))
    .catch(() => [])
  const next: External = { surfaced, sections }
  const key = contentKey(next)
  if (key === lastExternal) return
  lastExternal = key
  await update($, external, () => next)
}

const unwrapText = (text: string) => {
  const value = JSON.parse(text)
  return value && value.schema_version === 1 ? value.data : value
}

// Questions the answer puts to the person: sentences ending in "?", outside code. The quick
// first pass; the small-model pass below rewrites, merges or drops them.
function questionsIn(text: string): string[] {
  const found: string[] = []
  let isCode = false
  for (const raw of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(raw)) {
      isCode = !isCode
      continue
    }
    if (isCode || /^\s*(>|\|)/.test(raw)) continue
    const line = raw.replace(/^\s*([-*+]|\d+[.)])\s+/, '').replace(/\*\*|__|`/g, '').trim()
    for (const sentence of line.split(/(?<=[.!?:])\s+/)) {
      if (sentence.endsWith('?') && sentence.length >= 12 && sentence.length <= 300) found.push(sentence)
    }
  }
  return found.slice(-8)
}

const answerAsks = (view: View) => view.asks.filter(ask => ask.from === 'answer')

// After the person writes, one small-model call says which open questions the reply settles:
// answered, or made irrelevant by a change of plan. Unsure or failed: they stay.
async function settle($: Engine, reply: string) {
  const open = answerAsks((await read($, views))[MAIN] ?? EMPTY)
  if (open.length === 0) return
  const list = open.map((ask, i) => `${i + 1}. ${ask.summary ?? ask.text}`).join('\n')
  const r = await $.model
    .complete({
      model: 'haiku',
      maxTokens: 40,
      timeoutMs: 20000,
      system: 'You track which questions a person still needs to answer. A question is closed when their message answers it, or when it changes the plan so the question no longer matters. Reply with the numbers of the closed questions, comma-separated, or "none". Nothing else.',
      prompt: `Open questions:\n${list}\n\nThe person's message:\n${reply.slice(0, 4000)}`,
    })
    .catch(() => undefined)
  if (!r?.isAnswered) return
  const settled = new Set([...r.text.matchAll(/\d+/g)].map(m => open[Number(m[0]) - 1]?.id).filter(Boolean))
  if (settled.size > 0) await edit($, MAIN, view => ({ ...view, asks: view.asks.filter(ask => !settled.has(ask.id)) }))
}

const REFINE_SYSTEM =
  'You keep a side panel for a person working with a coding assistant, so nothing they still need to answer gets lost in the scroll. Reply with one JSON object and nothing else.'

type Refined = { title?: string; recap?: string; settled?: string[]; items?: { id?: string; summary?: string; detail?: string }[] }

function parseRefined(text: string): Refined | undefined {
  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) return undefined
  try {
    const value = JSON.parse(text.slice(start, end + 1))
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Refined) : undefined
  } catch {
    return undefined
  }
}

const clip = (value: unknown, limit: number) => (typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, limit) : '')

// After each main-conversation turn, one small-model call keeps the top of the pane and the
// Waiting on list current: the title, the recap, which open items the turn settled, and a
// summary plus detail for each item still open. A failed call or a reply that doesn't parse
// changes nothing.
async function refine($: Engine, answer: string) {
  const view = (await read($, views))[MAIN] ?? EMPTY
  const before = await read($, meta)
  const prompt = await read($, lastPrompt)
  const open = answerAsks(view)
  const r = await $.model
    .complete({
      model: 'haiku',
      maxTokens: 900,
      timeoutMs: 30000,
      system: REFINE_SYSTEM,
      prompt: [
        `Conversation title so far: ${before.title || '(none)'}`,
        `Recap so far: ${before.recap || '(none)'}`,
        'Open items the assistant is waiting on the person for (id: item):',
        open.length ? open.map(ask => `${ask.id}: ${ask.summary ?? ask.text}`).join('\n') : '(none)',
        `The person's latest message:\n"""\n${prompt.slice(0, 3000)}\n"""`,
        `The assistant's latest reply:\n"""\n${answer.slice(0, 6000)}\n"""`,
        'Return JSON: {"title": "a 3-6 word name for the conversation", "recap": "1-2 sentences: what this conversation is doing and where it stands now", "settled": ["ids of open items that are answered, done or acknowledged, or that the message or the reply made irrelevant (the plan changed, the option went away, the question no longer applies)"], "items": [{"id": "an open item\'s id, or \\"new\\"", "summary": "one line under 90 characters: what the person needs to decide or provide, specific enough to answer without scrolling back", "detail": "under 400 characters: the options, context and specifics needed to answer"}]}',
        'items: every open item not settled, plus anything new the reply asks of the person or waits on them for. Leave out rhetorical questions and work the assistant will do itself. When the person changes direction, drop what the old plan needed from them; an item that is only half still relevant gets a rewritten summary. One item per ask: never combine two topics or decisions into one item; split them.',
      ].join('\n\n'),
    })
    .catch(() => undefined)
  if (!r?.isAnswered) return
  const got = parseRefined(r.text)
  if (!got) return
  await update($, meta, m => ({
    ...m,
    title: m.sessionTitle || clip(got.title, 60) || m.title,
    recap: clip(got.recap, 400) || m.recap,
    recapFor: view.answerAt,
  }))
  const settled = new Set(Array.isArray(got.settled) ? got.settled.filter(id => typeof id === 'string') : [])
  const items = Array.isArray(got.items) ? got.items.slice(0, 10) : []
  const now = await $.clock.now()
  await edit($, MAIN, v => {
    let asks = v.asks.filter(ask => !(ask.from === 'answer' && settled.has(ask.id)))
    items.forEach(item => {
      const summary = clip(item.summary, 120)
      const detail = clip(item.detail, 500)
      if (!summary) return
      const known = asks.find(ask => ask.from === 'answer' && ask.id === item.id)
      if (known) {
        asks = asks.map(ask => (ask === known ? { ...ask, summary, detail: detail || ask.detail } : ask))
      } else if (!asks.some(ask => sameText(ask.summary ?? ask.text, summary))) {
        asks = [...asks, { id: `q:${now}:${++askSeq}`, text: summary, from: 'answer', summary, detail }]
      }
    })
    return { ...v, asks }
  })
}

// Close asks by id. Background-agent rows aren't closable here: they leave when the agent stops.
// The ask, title then description, into the prompt box, where placeInDraft says.
async function pasteAsk($: Engine, entry: string) {
  const box = await $.prompt.read()
  await $.prompt.fill(placeInDraft(box.text, box.cursor, entry))
}

async function closeAsks($: Engine, key: string, ids: readonly string[]) {
  const wanted = new Set(ids)
  let closed: string[] = []
  await edit($, key, view => {
    closed = view.asks.filter(ask => ask.from !== 'agent' && wanted.has(ask.id)).map(ask => ask.id)
    return { ...view, asks: view.asks.filter(ask => !closed.includes(ask.id)) }
  })
  await update($, openAsks, list => list.filter(id => !closed.includes(id)))
  return closed
}

// Background agents the main loop started and is still waiting on.
async function refreshAgents($: Engine) {
  const list = await $.agent.list().catch(() => undefined)
  if (!list) return
  const running = list.filter(agent => ['pending', 'running', 'waiting'].includes(agent.status))
  const asks: Ask[] = running.map(agent => ({ id: `agent:${agent.id}`, text: `${agent.description} (${agent.type})`, from: 'agent' }))
  await edit($, MAIN, view => ({ ...view, asks: [...view.asks.filter(ask => ask.from !== 'agent'), ...asks] }))
}

async function refreshHarness($: Engine) {
  const cwd = await $.session.cwd().catch(() => '')
  const model = await $.session.model().catch(() => '')
  let branch = ''
  try {
    const git = await $.process.run(['git', '-C', cwd, 'branch', '--show-current'], { timeoutMs: 3000 })
    if (git.exitCode === 0) branch = git.stdout.trim()
  } catch {
    // not a repo, or no git
  }
  await update($, harness, () => ({ model, cwd, branch }))
}

const pick = (not?: string) => {
  const choices = CLAUDES.filter(claude => claude.name !== not)
  return choices[Math.floor(Math.random() * choices.length)] ?? CLAUDES[0]
}

async function poke($: Engine) {
  const now = await read($, corner)
  const claude = CLAUDES.find(c => c.name === now.name) ?? CLAUDES[0]
  if (!claude) return
  const action = claude.action
  if (action.kind === 'next') {
    await update($, corner, () => ({ name: pick(claude.name)?.name ?? claude.name, frame: 0 }))
    return
  }
  const message =
    action.kind === 'toast' ? action.text : action.kind === 'recap' ? (await read($, meta)).recap || 'No recap yet: it comes after the first answer.' : ''
  if (message) {
    $.ui.toast(message)
    // A short message also shows in the speech row above the Claude, for a few seconds.
    const shown = [...message].length <= WIDTH - 4 ? message : ''
    await update($, said, () => shown)
    if (shown) $.clock.after(SAID_MS, () => void update($, said, now => (now === shown ? '' : now)))
  }
  // a short wiggle on every click; 'animate' plays longer. Ends on the first frame.
  const flips = action.kind === 'animate' ? 6 : 2
  const count = claude.frames.length || 1
  for (let i = 1; i <= flips; i++) {
    $.clock.after(i * 350, () => void update($, corner, c => ({ ...c, frame: i < flips ? i % count : 0 })))
  }
}

async function togglePin($: Engine, id: string, text: string) {
  const at = await $.clock.now()
  await update($, pins, list => (list.some(pin => pin.id === id) ? list.filter(pin => pin.id !== id) : [...list, { id, text, at }]))
  void mirror($)
}

// A press on a response row: bring that reply into view. A reply the transcript no longer
// draws (compacted, cleared) leaves the list, and the person is told.
async function jump($: Engine, id: string) {
  const moved = await $.ui.scroll({ to: { requestId: id }, block: 'start' }).catch((error: unknown) => ({ deny: String(error) }))
  if (!moved.deny) return
  const isGone = !/person|surface|meanwhile/i.test(moved.deny)
  if (isGone) {
    await update($, responses, list => list.filter(r => r.id !== id))
    await update($, openResponses, list => list.filter(open => open !== id))
  }
  $.ui.toast(isGone ? 'That reply is no longer in the transcript.' : `Could not jump: ${moved.deny}`)
}

// These hooks only watch. If one fails, the call goes on as if it weren't here:
// next(e) replays what the call already settled to, so nothing runs twice.
function passThrough($: Engine, e: any, next: (e: any) => any) {
  return next(e)
}

function open($: Engine) {
  return $.ui.open({ id: PANE, title: 'Info pane', columns: WIDTH, rows: 2 })
}

// Run from the plugin's one session.start hook.
export async function startInfoPane($: Engine) {
  await $.command.register({ name: 'info-pane', description: 'Open the info pane (recap, surfaced asks and notes, pins, tasks, responses)' })
  await $.tool
    .register({
      name: 'asks',
      description:
        "The info pane's Waiting on list: what you asked the person and are waiting on. Call it with no input to list the open items and their ids. Pass close: [ids] for items the person answered, decided, deferred or made moot (a change of plan), so they leave the pane. Use it when the person answers something you asked earlier, or asks you to clear the pane.",
      inputSchema: {
        type: 'object',
        properties: { close: { type: 'array', items: { type: 'string' }, description: 'ids of items to close' } },
        additionalProperties: false,
      },
    })
    .catch(() => undefined)
  await restore($)
  // A reload drops this module's timers, a queued recap pass with them. If the last answer
  // has no recap yet, queue it again now.
  const last = (await read($, views))[MAIN]
  if (last?.answer && (await read($, meta)).recapFor !== last.answerAt) {
    const owed = last.answer
    $.clock.after(1500, () => void refine($, owed))
  }
  syncSpinner($)
  void refreshHarness($)
  void refreshExternal($).catch(() => undefined)
  $.clock.every(POLL_MS, () => void refreshExternal($).catch(() => undefined))
  const now = await read($, corner)
  if (!CLAUDES.some(c => c.name === now.name)) await update($, corner, () => ({ name: pick()?.name ?? '', frame: 0 }))
  void open($)
}

export function registerInfoPane(on: On) {

  // The harness's own name for the session, when it has one (a rename, a resumed title).
  on('classic.SessionStart', async ($, e, next) => {
    const transcript = (e as { transcript_path?: unknown }).transcript_path
    if (typeof transcript === 'string') sourceTranscript = transcript
    const title = clip((e as { session_title?: unknown }).session_title, 60)
    if (title) await update($, meta, m => ({ ...m, sessionTitle: title, title }))

    return next(e)
  }).catch(passThrough)

  on('command.run', { command: 'info-pane' }, async $ => {
    const opened = await open($)

    return { text: opened.isPlaced ? 'Info pane open.' : `Info pane waits: ${opened.reason}` }
  })

  on('turn.start', async ($, e, next) => {
    void refreshHarness($)

    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    if (e.text.trim()) {
      await update($, lastPrompt, () => e.text)
      $.clock.after(0, () => void settle($, e.text))
    }

    return next(e)
  }).catch(passThrough)

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    const key = keyOf(e.agentId)
    const answer = e.answer || done.text
    if (answer) {
      const now = await $.clock.now()
      await edit($, key, view => {
        if (key !== MAIN) return { ...view, answer, answerAt: now }
        const fresh = questionsIn(answer).filter(text => !view.asks.some(ask => sameText(ask.summary ?? ask.text, text)))
        const asks = [...view.asks, ...fresh.map((text): Ask => ({ id: `q:${now}:${++askSeq}`, text, from: 'answer' }))]
        return { ...view, answer, answerAt: now, asks }
      })
      if (key === MAIN) {
        await update($, isAnswerOpen, () => false)
        $.clock.after(0, () => void refine($, answer))
      }
    }
    void refreshAgents($)

    return done
  })

  on('tool.call', { tool: ASKS_TOOL }, async ($, e) => {
    const key = keyOf(e.agentId)
    const input = e as unknown as { close?: unknown }
    const ids = Array.isArray(input.close) ? input.close.filter((id): id is string => typeof id === 'string') : []
    const closed = ids.length ? await closeAsks($, key, ids) : []
    const open = ((await read($, views))[key] ?? EMPTY).asks.map(ask => ({ id: ask.id, item: ask.summary ?? ask.text, from: ask.from }))
    const unknown = ids.filter(id => !closed.includes(id))
    // An MCP-style tool answers text (a string or content blocks); an object is refused.
    return { result: JSON.stringify({ closed, ...(unknown.length ? { notClosed: unknown } : {}), open }) }
  })

  // An explicit question dialog: its questions wait in the pane while it is open.
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    const key = keyOf(e.agentId)
    const id = `dialog:${e.tool_use_id ?? (await $.clock.now())}`
    const asks = e.questions.map((q, i): Ask => ({
      id: `${id}:${i}`,
      text: q.question,
      from: 'dialog',
      detail: q.options?.map(option => option.label).join(' · '),
    }))
    await edit($, key, view => ({ ...view, asks: [...view.asks, ...asks] }))
    try {
      return await next(e)
    } finally {
      await edit($, key, view => ({ ...view, asks: view.asks.filter(ask => !ask.id.startsWith(id)) }))
    }
  }).catch(passThrough)

  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    const ran = await next(e)
    void refreshAgents($)

    return ran
  }).catch(passThrough)

  on('tool.call', { tool: 'TaskCreate' }, async ($, e, next) => {
    const ran = await next(e)
    const id = 'result' in ran ? (ran.result as { task?: { id?: string } } | undefined)?.task?.id : undefined
    if (id) {
      const task: Task = { id, subject: e.subject, status: 'pending', active: e.activeForm, detail: firstLine(e.description), from: 'task' }
      await edit($, keyOf(e.agentId), view => ({ ...view, tasks: [...view.tasks.filter(t => t.id !== id), task] }))
    }

    return ran
  }).catch(passThrough)

  on('tool.call', { tool: 'TaskUpdate' }, async ($, e, next) => {
    const ran = await next(e)
    if ('deny' in ran && ran.deny) return ran
    await edit($, keyOf(e.agentId), view => ({
      ...view,
      tasks:
        e.status === 'deleted'
          ? view.tasks.filter(t => t.id !== e.taskId)
          : view.tasks.map(t =>
              t.id !== e.taskId
                ? t
                : {
                    ...t,
                    subject: e.subject ?? t.subject,
                    status: e.status === undefined || e.status === 'deleted' ? t.status : e.status,
                    active: e.activeForm ?? t.active,
                    detail: e.description !== undefined ? firstLine(e.description) : t.detail,
                  },
            ),
    }))

    return ran
  }).catch(passThrough)

  on('tool.call', { tool: 'TodoWrite' }, async ($, e, next) => {
    const ran = await next(e)
    const todos: Task[] = e.todos.map((todo, i) => ({ id: `todo:${i}`, subject: todo.content, status: todo.status, active: todo.activeForm, from: 'todo' }))
    await edit($, keyOf(e.agentId), view => ({ ...view, tasks: [...view.tasks.filter(t => t.from !== 'todo'), ...todos] }))

    return ran
  }).catch(passThrough)

  // Each reply in the transcript: a pin glyph in its own column (◇ to pin, ◆ pinned); the
  // reply joins the Responses tab; and whether the rows of the current last answer are on
  // screen, for the pane to know.
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const drawn = await next(e)
    const text = e.props.text.trim()
    if (!text) return drawn
    // Writes wait for the draw to finish: a render hook never writes.
    const isOffScreen = e.props.onScreen === null || e.props.onScreen === undefined
    if (isOffScreen && !isFilledKnown) {
      isFilledKnown = true
      $.clock.after(0, () => void update($, screenFilled, () => true))
    }
    if (e.props.isFirstOfReply && viewing === MAIN && !e.props.isSummary) {
      const id = e.requestId
      $.clock.after(0, () => void update($, responses, list => remember(list, id, text)))
    }
    const isPinned = (await read($, pins)).some(pin => pin.id === e.requestId)
    const { Box, Button } = $.ui.resolve(e)

    return (
      <Box flexDirection="row">
        <Box flexGrow={1} flexShrink={1}>
          {drawn}
        </Box>
        <Button key={`pin:${e.requestId}`} plain label={isPinned ? '◆' : '◇'} onPress={() => togglePin($, e.requestId, e.props.text)} />
      </Box>
    )
  }).catch(passThrough)

  // Keyboard focus on the little Claude shows its click hint, as a pointer over it does.
  on('ui.focus', { requestId: PANE }, async ($, e, next) => {
    const moved = await next(e)
    await update($, mascotFocus, () => e.element === MASCOT || e.element?.startsWith(`${MASCOT}:`) === true)

    return moved
  }).catch(passThrough)

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Markdown, Button } = $.ui.resolve(e)
    const key = keyOf(e.props.view.agentId)
    viewing = key
    const isTerminal = e.surface === 'terminal'
    const width = e.props.bodyColumns

    // No dock here (main-screen layout): one line, not a third of the screen.
    if (isTerminal && e.props.placement === 'inline' && !e.props.isFocused) {
      return <Text dimColor wrap="truncate-end">Info pane docks in fullscreen (/tui).</Text>
    }

    const shownTab = key === MAIN ? await read($, tab) : 'overview'
    const replies = await read($, responses)
    const head = (label: string, count?: number) => (
      <Text bold color="claude">
        {label}
        {count === undefined ? '' : <Text dimColor>{` ${count}`}</Text>}
      </Text>
    )
    const tabs =
      key === MAIN ? (
        <Box flexDirection="row" gap={2}>
          <Button key="tab:overview" plain hotkey="o" onPress={() => update($, tab, () => 'overview')}>
            <Text bold={shownTab === 'overview'} underline={shownTab === 'overview'}>
              Overview
            </Text>
          </Button>
          <Button key="tab:responses" plain hotkey="r" onPress={() => update($, tab, () => 'responses')}>
            <Text bold={shownTab === 'responses'} underline={shownTab === 'responses'}>
              {`Responses ${replies.length}`}
            </Text>
          </Button>
        </Box>
      ) : null

    if (shownTab === 'responses') {
      const expanded = await read($, openResponses)
      return (
        <Box flexDirection="column" width={isTerminal ? width : undefined} minHeight={isTerminal && e.props.placement === 'dock' ? e.props.scroll.bodyRows : undefined} gap={1}>
          {tabs}
          <Box flexDirection="column">
            {replies.length === 0 ? <Text dimColor>No responses yet</Text> : null}
            {replies.map((reply: Response) => {
              const isOpen = expanded.includes(reply.id)
              return (
                <Box key={`response:${reply.id}`} flexDirection="column">
                  <Box flexDirection="row">
                    <Button
                      key={`toggle:${reply.id}`}
                      plain
                      label={isOpen ? '[-]' : '[+]'}
                      onPress={() => update($, openResponses, list => (list.includes(reply.id) ? list.filter(id => id !== reply.id) : [...list, reply.id]))}
                    />
                    <Text> </Text>
                    <Box flexGrow={1} flexShrink={1}>
                      <Button key={`jump:${reply.id}`} plain onPress={() => jump($, reply.id)}>
                        <Text wrap="truncate-end">{clipRow(reply.title, width - 4)}</Text>
                      </Button>
                    </Box>
                  </Box>
                  {isOpen
                    ? wrapRows(`Summary: ${reply.summary}`, Math.max(8, width - 2), 4).map(line => (
                        <Text dimColor wrap="truncate-end">{`  ${line}`}</Text>
                      ))
                    : null}
                </Box>
              )
            })}
          </Box>
          <Text dimColor wrap="truncate-end">
            [+]/[-] summary · row jumps · o/r tabs
          </Text>
        </Box>
      )
    }

    const view = (await read($, views))[key] ?? EMPTY
    const where = await read($, harness)
    const isOpen = await read($, isAnswerOpen)
    const isFilled = await read($, screenFilled)
    const spark = SPARK[(await read($, spin)) % SPARK.length] ?? '✻'
    const { title, recap } = await read($, meta)
    const pinned = await read($, pins)
    const expanded = await read($, openAsks)
    const now = await read($, corner)
    const claude = CLAUDES.find(c => c.name === now.name) ?? CLAUDES[0]
    const sprite = claude?.frames[now.frame % (claude.frames.length || 1)] ?? []
    const spoken = await read($, said)
    const isMascotFocused = e.props.isFocused && (await read($, mascotFocus))
    const shared = await read($, external)
    const surfaced = key === MAIN ? shared.surfaced : []
    const surfacedAsks = surfaced.filter(item => item.kind === 'ask')
    const surfacedNotes = surfaced.filter(item => item.kind !== 'ask')
    // A captured ask the agent also surfaced shows once, under Asks.
    const waiting = view.asks.filter(ask => !surfacedAsks.some(item => sameText(item.text, ask.summary ?? ask.text) || sameText(item.text, ask.text)))

    let heading = title || 'This conversation'
    if (e.props.view.agentId) {
      const agent = (await $.agent.list().catch(() => [])).find(a => a.id === e.props.view.agentId)
      heading = agent ? `Agent: ${agent.description}` : 'Agent'
    }
    const home = (await $.env.get('HOME').catch(() => undefined)) ?? ''
    const cwd = where.cwd.startsWith(home) && home ? `~${where.cwd.slice(home.length)}` : where.cwd
    let sections = key === MAIN ? shared.sections : []
    if (key !== MAIN) {
      try {
        const parent = await $.session.id()
        const agent = e.props.view.agentId ?? ''
        const native = /^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/.test(agent)
        const session = native ? agent : `${parent}-sub-${agent}`
        if (SAFE_ID.test(session)) sections = parseSections(JSON.stringify(unwrapText(await $.fs.read(sectionsFile(await stateRoot($), session)))))
      } catch {
        /* Optional sections cannot prevent the built-in pane from rendering. */
      }
    }

    const room = Math.max(6, Math.floor((e.viewport?.rows ?? 40) * 0.35))
    const answerText = narrowTables(view.answer)
    const lines = answerText.split('\n')
    const isLong = lines.length > room
    const shown = isLong && !isOpen ? lines.slice(0, room).join('\n') : answerText

    const inProgress = view.tasks.filter(t => t.status === 'in_progress')
    const notStarted = view.tasks.filter(t => t.status === 'pending')
    const done = view.tasks.filter(t => t.status === 'completed')
    const pinnedText = (text: string) => {
      const firstLines = narrowTables(text).trim().split('\n').filter(Boolean).slice(0, 3).join(' ')
      return firstLines.length > width * 3 ? `${firstLines.slice(0, width * 3 - 1)}…` : firstLines
    }
    const surfacedRow = (item: (typeof surfaced)[number]) => (
      <Text wrap="wrap">
        <Text color="claude" bold>{`${item.badge} `}</Text>
        {item.kind === 'decision' ? `Decision: ${item.text}` : item.text}
      </Text>
    )

    return (
      <Box flexDirection="column" width={isTerminal ? width : undefined} minHeight={isTerminal && e.props.placement === 'dock' ? e.props.scroll.bodyRows : undefined} gap={1}>
        {tabs}
        <Box flexDirection="column">
          {/* The speech row and a blank gap, always reserved so nothing moves: what the last
              click said, or, on hover or keyboard focus, what a click does. */}
          {claude ? (
            <Box key="speech" flexDirection="column" height={isTerminal ? 2 : undefined}>
              {spoken ? (
                <Text color={claude.color} wrap="truncate-end">{`( ${spoken} )`}</Text>
              ) : isMascotFocused ? (
                <Text dimColor wrap="truncate-end">{`( ${HINTS[claude.action.kind]} )`}</Text>
              ) : (
                <>
                  {!isTerminal ? <Text>{'\u00a0'}</Text> : null}
                  <Box position="absolute" top={0} left={0} display="none" hover={{ scope: MASCOT, display: 'flex' }}>
                    <Text dimColor wrap="truncate-end">{`( ${HINTS[claude.action.kind]} )`}</Text>
                  </Box>
                </>
              )}
              {!isTerminal ? <Text>{'\u00a0'}</Text> : null}
            </Box>
          ) : null}
          <Box flexDirection="row" flexWrap={isTerminal ? undefined : 'wrap'} gap={1}>
            {/* The pane docks on the right, so its little Claude sits on the left. */}
            {claude ? (
              <Box flexShrink={0} hover={{ scope: MASCOT }}>
                {isTerminal ? (
                  <Button key="little-claude" plain hover={SPRITE_HOVER} onPress={() => poke($)}>
                    <Text color={claude.color}>{sprite.join('\n')}</Text>
                  </Button>
                ) : (
                  <Box flexDirection="column">
                    {sprite.map((row, i) => (
                      <Button key={`${MASCOT}:${i}`} plain hover={SPRITE_HOVER} label={i === 0 ? `Little Claude: ${claude.name}` : undefined} onPress={() => poke($)}>
                        <Text mono color={claude.color}>{row.replace(/ /g, '\u00a0')}</Text>
                      </Button>
                    ))}
                  </Box>
                )}
              </Box>
            ) : null}
            <Box flexDirection="column" flexGrow={1} flexShrink={1} minWidth={isTerminal ? undefined : 18}>
              <Text bold wrap="truncate-end" color={key === MAIN ? undefined : 'warning'}>
                {heading}
              </Text>
              <Text dimColor wrap="truncate-end">
                Claude Code · {where.model || '…'}
              </Text>
              <Text dimColor wrap="truncate-middle">
                {cwd || ' '}
                {where.branch ? ` (${where.branch})` : ''}
              </Text>
            </Box>
          </Box>
          {key === MAIN && recap ? <Text wrap="wrap">{recap}</Text> : null}
        </Box>

        {surfacedAsks.length > 0 ? (
          <Box flexDirection="column">
            {head('Asks', surfacedAsks.length)}
            {surfacedAsks.map(surfacedRow)}
          </Box>
        ) : null}

        {surfacedNotes.length > 0 ? (
          <Box flexDirection="column">
            {head('Notes', surfacedNotes.length)}
            {surfacedNotes.map(surfacedRow)}
          </Box>
        ) : null}

        {sections.map(section => (
          <Box flexDirection="column">
            {head([section.icon, section.title].filter(Boolean).join(' '))}
            {section.rows.map(row => (
              <Text wrap="wrap">
                {row.label ? `${row.label}: ` : ''}
                {row.value}
              </Text>
            ))}
          </Box>
        ))}

        {pinned.length > 0 ? (
          <Box flexDirection="column">
            {head('Pinned', pinned.length)}
            {pinned.map((pin: Pin) => (
              <Button key={`unpin:${pin.id}`} plain onPress={() => togglePin($, pin.id, pin.text)}>
                <Text color="claude">◆ </Text>
                <Text>{pinnedText(pin.text)}</Text>
              </Button>
            ))}
          </Box>
        ) : null}

        {view.answer && isFilled ? (
          <Box flexDirection="column">
            {head('Last answer')}
            <Markdown text={shown} />
            {isLong ? (
              <Button key="more" hotkey="m" plain label={isOpen ? 'show less' : `show all ${lines.length} lines`} onPress={() => update($, isAnswerOpen, value => !value)} />
            ) : null}
          </Box>
        ) : null}

        {waiting.length > 0 ? (
          <Box flexDirection="column">
            {head('Waiting on', waiting.length)}
            {waiting.map(ask => {
              const isExpanded = expanded.includes(ask.id)
              const more = ask.detail || (ask.summary && ask.summary !== ask.text ? ask.text : '')
              return (
                <Box flexDirection="column">
                  <Button
                    key={`ask:${ask.id}`}
                    plain
                    onPress={() => update($, openAsks, list => (list.includes(ask.id) ? list.filter(id => id !== ask.id) : [...list, ask.id]))}
                  >
                    <Text color={ask.from === 'agent' ? 'suggestion' : 'warning'}>{ask.from === 'agent' ? '◷ ' : isExpanded && more ? '▾ ' : '☐ '}</Text>
                    <Text>{ask.summary ?? ask.text}</Text>
                  </Button>
                  {/* The ✕ sits in the indent before the description; a click on the
                      description puts the ask in the prompt box (draft.ts). */}
                  {isExpanded && (more || ask.from !== 'agent') ? (
                    <Box flexDirection="row">
                      {ask.from !== 'agent' ? (
                        <Button key={`dismiss:${ask.id}`} plain dimColor label="✕" onPress={() => void closeAsks($, key, [ask.id])} />
                      ) : (
                        <Text> </Text>
                      )}
                      <Text> </Text>
                      {more ? (
                        <Box flexShrink={1}>
                          <Button
                            key={`paste:${ask.id}`}
                            plain
                            dimColor
                            label={more}
                            onPress={() => void pasteAsk($, askEntry(ask.summary ?? ask.text, more)).catch(() => $.ui.toast("Couldn't reach the prompt box"))}
                          />
                        </Box>
                      ) : null}
                    </Box>
                  ) : null}
                </Box>
              )
            })}
          </Box>
        ) : null}

        {inProgress.length > 0 ? (
          <Box flexDirection="column">
            <Text bold color="claude">
              {`${spark} In progress`}
              <Text dimColor>{` ${inProgress.length}`}</Text>
            </Text>
            {inProgress.map(t => (
              <Box flexDirection="column">
                <Text wrap="wrap">
                  <Text color="claude">{`${spark} `}</Text>
                  {t.subject}
                </Text>
                {t.active || t.detail ? (
                  <Text dimColor wrap="truncate-end">
                    {'  '}
                    {t.active ?? t.detail}
                  </Text>
                ) : null}
              </Box>
            ))}
          </Box>
        ) : null}

        {notStarted.length > 0 ? (
          <Box flexDirection="column">
            {head('Not started', notStarted.length)}
            {notStarted.map(t => (
              <Text wrap="truncate-end">○ {t.subject}</Text>
            ))}
          </Box>
        ) : null}

        {done.length > 0 ? (
          <Box flexDirection="column">
            {head('Done', done.length)}
            {done.slice(-12).map(t => (
              <Text dimColor strikethrough wrap="truncate-end">
                ✓ {t.subject}
              </Text>
            ))}
            {done.length > 12 ? <Text dimColor>{`  +${done.length - 12} earlier`}</Text> : null}
          </Box>
        ) : null}
      </Box>
    )
  })
}

function firstLine(text: string | undefined) {
  const line = (text ?? '').split('\n').find(l => l.trim())?.trim()
  return line ? line.slice(0, 200) : undefined
}
