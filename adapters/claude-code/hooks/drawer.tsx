// Prompt context drawer. A row above the prompt names the prompt behind the replies in view:
// the nearest prompt at or above the first transcript row on screen (the latest prompt where
// the surface does not report what is on screen).
//
// - Hover the row: the full prompt opens upward from it as a card painted over the bottom of
//   the transcript, anchored to the last row on screen. Nothing moves: the card is absolutely
//   positioned and revealed by a shared hover scope, so no hook runs and no layout changes.
//   Leaving closes it. (The top of the fullscreen transcript is the engine's own sticky prompt
//   header, which no plugin can draw into.)
// - Click the row or the card: the transcript jumps to that prompt.
// - Keyboard, or a prompt longer than the card: /context-drawer (or the row's "read" button)
//   opens it in a focused pane. Arrows and page keys scroll inside it, Enter jumps, Esc closes.
//
// What the engine cannot do, and this does not pretend to: a hover card cannot scroll (hover
// runs no hook), and it is clipped to the transcript's viewport. See README.md.
import { atom, memberOf, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import type { Row } from '../types'

type On = Parameters<Register>[0]

export const DRAWER = 'context-drawer'
const SCOPE = 'context-drawer'
const PROMPT_KINDS = new Set(['composer', 'bridge', 'sdk'])

const rows = atom({ plugin: 'info-pane', key: 'rows' } as const, [])
const onScreen = atom({ plugin: 'info-pane', key: 'onScreen' } as const, [])
const drawerFor = atom({ plugin: 'info-pane', key: 'drawerFor' } as const, '')
// Per transcript row: whether it hosts the card (the last row on screen).
const cardHost = atom({ plugin: 'info-pane', key: 'cardHost' } as const, false)

let host = ''
let isScreenKnown = false
let isMainView = true

// A slash command's echo (`/context-drawer`, `/model opus`) is a command, not a prompt.
const COMMAND = /^\/[A-Za-z][\w:-]*(\s|$)/

export const isPrompt = (props: { text: string; origin: { kind: string }; from?: unknown; task?: unknown }) =>
  !COMMAND.test(props.text.trim()) && (PROMPT_KINDS.has(props.origin.kind) || (props.origin.kind === 'unclassified' && !props.from && !props.task))

// The prompt behind what is on screen: the nearest prompt at or before the first visible row;
// and the last visible row, which hosts the card.
export function contextOf(list: readonly Row[], visible: readonly string[], isKnown: boolean): { prompt?: Row; last?: Row } {
  const isContext = (row: Row) => row.kind === 'prompt' && !COMMAND.test(row.text.trim())
  const prompts = list.filter(isContext)
  if (!isKnown) return { prompt: prompts.at(-1) }
  const index = list.findIndex(row => visible.includes(row.id))
  if (index < 0) return { prompt: prompts.at(-1) }
  const prompt = [...list.slice(0, index + 1)].reverse().find(isContext)
  const last = [...list].reverse().find(row => visible.includes(row.id))
  return { prompt, last }
}

// At most LIMIT rows; the oldest rows that are not prompts go first, so the context of what
// is on screen survives a long run of tool calls.
const LIMIT = 500
function bounded(list: Row[]): Row[] {
  let extra = list.length - LIMIT
  if (extra <= 0) return list
  const kept = list.filter(row => row.kind === 'prompt' || extra-- <= 0)
  return kept.length > LIMIT ? kept.slice(-LIMIT) : kept
}

async function observe($: Engine, row: Row, seen: { first: number; last: number; of: number } | null | undefined) {
  if (!isMainView) return
  const list = await read($, rows)
  const known = list.find(r => r.id === row.id)
  if (!known || known.text !== row.text) {
    await update($, rows, now => (now.some(r => r.id === row.id) ? now.map(r => (r.id === row.id ? row : r)) : bounded([...now, row])))
  }
  if (seen !== undefined) {
    isScreenKnown = true
    const ids = await read($, onScreen)
    const has = ids.includes(row.id)
    if (seen && !has) await update($, onScreen, now => [...now, row.id])
    if (!seen && has) await update($, onScreen, now => now.filter(id => id !== row.id))
  }
  await settle($)
}

// Writes only what changed: every write redraws its readers, and the rows that draw re-report.
async function settle($: Engine) {
  const { prompt, last } = contextOf(await read($, rows), await read($, onScreen), isScreenKnown)
  const id = prompt?.id ?? ''
  if ((await read($, drawerFor)) !== id) await update($, drawerFor, () => id)
  const next = last?.id ?? ''
  if (host === next) return
  if (host) await update($, memberOf(cardHost, { requestId: host }), () => false)
  host = next
  if (next) await update($, memberOf(cardHost, { requestId: next }), () => true)
}

async function jump($: Engine, id: string) {
  const moved = await $.ui.scroll({ to: { requestId: id }, block: 'start' }).catch((error: unknown) => ({ deny: String(error) }))
  if (moved.deny) $.ui.toast(`Could not jump to the prompt: ${moved.deny}`)
  return !moved.deny
}

async function promptText($: Engine) {
  const id = await read($, drawerFor)
  return (await read($, rows)).find(row => row.id === id)
}

function openDrawer($: Engine) {
  // The info pane's width, so a dock that already holds it keeps its size and the transcript
  // does not reflow.
  return $.ui.open({ id: DRAWER, title: 'Prompt', columns: 44, focus: true, closeOnEscape: true, holdToasts: true, rows: 12 })
}

// Rough wrap, for counting what fits in the card; the Text element does the real wrapping.
const wrapCount = (text: string, width: number) => text.split('\n').reduce((n, line) => n + Math.max(1, Math.ceil(line.length / Math.max(1, width))), 0)

function headLines(text: string, width: number, room: number): { shown: string; rest: number } {
  const lines = text.split('\n')
  const out: string[] = []
  let used = 0
  for (const line of lines) {
    const need = Math.max(1, Math.ceil(line.length / Math.max(1, width)))
    if (used + need > room) break
    out.push(line)
    used += need
  }
  return { shown: out.join('\n'), rest: wrapCount(text, width) - used }
}

// Any other transcript row (a tool call, its result, a command's output, a notice): it counts
// for where the card goes, never for which prompt is in context.
async function watch($: Engine, e: any, drawn: any) {
  const row: Row = { id: e.requestId, kind: 'other', text: '' }
  const seen = e.props.onScreen
  $.clock.after(0, () => void observe($, row, seen))
  return card($, e, drawn)
}

// The card, on the last transcript row on screen, rising from its bottom edge to sit just above
// the drawer's row. Every other row is drawn as it was.
async function card($: Engine, e: any, drawn: any) {
  if (!(await read($, memberOf(cardHost, e)))) return drawn
  const prompt = await promptText($)
  if (!prompt) return drawn
  const { Box, Button, Text } = $.ui.resolve(e)
  const width = Math.max(20, (e.viewport?.columns ?? 80) - 4)
  const room = Math.max(3, (e.viewport?.rows ?? 24) - 10)
  const { shown, rest } = headLines(prompt.text, width - 4, room - 3)
  return (
    <Box flexDirection="column">
      {drawn}
      <Box key="drawer-anchor" position="absolute" bottom={0} left={0} width={1} height={1} hover={{ scope: SCOPE }}>
        <Box
          position="absolute"
          bottom={0}
          left={0}
          width={width}
          display="none"
          hover={{ scope: SCOPE, display: 'flex' }}
          flexDirection="column"
          borderStyle="round"
          borderColor="claude"
          backgroundColor="background"
          paddingX={1}
        >
          <Button key="drawer:card" plain onPress={() => jump($, prompt.id)}>
            <Text wrap="wrap">{shown}</Text>
          </Button>
          <Text dimColor wrap="truncate-end">
            {rest > 0 ? `… ${rest} more lines · /context-drawer reads all · click jumps` : 'click jumps to this prompt'}
          </Text>
        </Box>
      </Box>
    </Box>
  )
}

// Run from the plugin's one session.start hook.
export async function startContextDrawer($: Engine) {
  await $.command.register({ name: 'context-drawer', description: 'Read the prompt behind the replies in view; Enter jumps to it' })
}

export function registerContextDrawer(on: On) {

  on('command.run', { command: 'context-drawer' }, async $ => {
    if (!(await promptText($))) return { text: 'No prompt to show yet.' }
    const opened = await openDrawer($)

    return { text: opened.isPlaced ? 'Prompt drawer open: arrows scroll, Enter jumps, Esc closes.' : `Prompt drawer waits: ${opened.reason}` }
  })

  // The transcript's rows as it draws them: their order, which are on screen, and the card on
  // the last one on screen. Prompts set the context; every row can host the card.
  on('ui.render', { component: 'UserMessage' }, async ($, e, next) => {
    const drawn = await next(e)
    if (!isPrompt(e.props) || !e.props.text.trim()) return watch($, e, drawn)
    const row: Row = { id: e.requestId, kind: 'prompt', text: e.props.text }
    const seen = e.props.onScreen
    $.clock.after(0, () => void observe($, row, seen))
    return card($, e, drawn)
  })

  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const drawn = await next(e)
    if (!e.props.text.trim()) return drawn
    const row: Row = { id: e.requestId, kind: 'reply', text: '' }
    const seen = e.props.onScreen
    $.clock.after(0, () => void observe($, row, seen))
    return card($, e, drawn)
  })

  on('ui.render', { component: 'ToolUse' }, async ($, e, next) => watch($, e, await next(e)))
  on('ui.render', { component: 'ToolResult' }, async ($, e, next) => watch($, e, await next(e)))
  on('ui.render', { component: 'ToolGroup' }, async ($, e, next) => watch($, e, await next(e)))
  on('ui.render', { component: 'CommandOutput' }, async ($, e, next) => watch($, e, await next(e)))
  on('ui.render', { component: 'TurnDuration' }, async ($, e, next) => watch($, e, await next(e)))
  on('ui.render', { component: 'InfoNotice' }, async ($, e, next) => watch($, e, await next(e)))

  // The row above the prompt. Always drawn once there is a prompt, so it never jumps in.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    isMainView = !e.props.view.agentId
    const below = await next(e)
    const prompt = isMainView && !e.props.hasSurvey ? await promptText($) : undefined
    if (!prompt) return below
    const { Box, Button, Text } = $.ui.resolve(e)
    const first = prompt.text.trim().split('\n')[0] ?? ''
    const more = prompt.text.trim().split('\n').length - 1
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" width={e.props.bodyColumns} hover={{ scope: SCOPE, backgroundColor: 'userMessageBackground' }}>
          <Box flexGrow={1} flexShrink={1}>
            <Button key="drawer:strip" plain onPress={() => jump($, prompt.id)}>
              <Text dimColor>{'› '}</Text>
              <Text wrap="truncate-end">{first}</Text>
              <Text dimColor>{more > 0 ? ` +${more} lines` : ''}</Text>
            </Button>
          </Box>
          <Button key="drawer:read" plain label=" read" onPress={() => void openDrawer($)} />
        </Box>
        {below}
      </Box>
    )
  })

  // The keyboard drawer: the whole prompt, scrolled inside the pane.
  on('ui.render', { component: 'Pane', requestId: DRAWER }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const prompt = await promptText($)
    if (!prompt) return <Text dimColor>No prompt to show yet.</Text>
    return (
      <Box flexDirection="column" width={e.props.bodyColumns} gap={1}>
        <Button
          key="drawer:jump"
          autoFocus
          hotkey="j"
          label="Jump to this prompt"
          onPress={async () => {
            if (await jump($, prompt.id)) await $.ui.close({ id: DRAWER })
          }}
        />
        <Text wrap="wrap">{prompt.text}</Text>
      </Box>
    )
  })
}
