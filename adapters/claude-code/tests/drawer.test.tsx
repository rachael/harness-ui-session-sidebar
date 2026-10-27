import { expect, mock, test } from 'claude-code/testing'

import { prompt, ran, reply } from './kit'
import type { Seen } from './kit'

const BAND = {
  plugin: 'info-pane',
  surface: 'terminal',
  component: 'AbovePrompt',
  requestId: 'band',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} },
  viewport: { columns: 120, rows: 40, isFullscreen: true },
} as const

const DRAWER_PANE = {
  plugin: 'info-pane',
  surface: 'terminal',
  component: 'Pane',
  requestId: 'context-drawer',
  props: { title: 'Prompt', isFocused: true, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 12 }, view: {} },
  viewport: { columns: 200, rows: 50, isFullscreen: true },
} as const

const FIRST = 'Add a dusk timer to the beacon.'
const SECOND = 'Now add a fog mode.\nIt should blink twice.\nAnd stop at dawn.'

// The engine beneath: transcript rows draw their text, the band draws nothing of its own.
function engine(on: any) {
  const clock = mock.clock(on)
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  for (const component of ['UserMessage', 'AssistantMessage'] as const) {
    on('ui.render', { component }, async ($: any, e: any) => {
      const { Text } = $.ui.resolve(e)
      return <Text>{e.props.text}</Text>
    })
  }
  on('ui.render', { component: 'AbovePrompt' }, async ($: any, e: any) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  return { clock, toasts }
}

async function conversation($: any, clock: any, screen: { p1?: Seen; r1?: Seen; p2?: Seen; r2?: Seen } = {}) {
  const rows = {
    p1: await $.ui.mount(prompt('p1', FIRST, screen.p1)),
    r1: await $.ui.mount(reply('r1', 'The timer reads the tide table.', screen.r1)),
    p2: await $.ui.mount(prompt('p2', SECOND, screen.p2)),
    r2: await $.ui.mount(reply('r2', 'Fog mode blinks twice.', screen.r2)),
  }
  await clock.advance(10)
  return rows
}

test('the row above the prompt names the latest prompt, with a read button; one row, always drawn', async ($, on) => {
  const { clock } = engine(on)
  const band = await $.ui.mount(BAND)
  // nothing to name yet: the band is the engine's
  expect(await band.find({ type: 'Button', key: 'drawer:strip' })).toBeUndefined()
  await conversation($, clock)
  const strip = await band.find({ type: 'Button', key: 'drawer:strip' })
  expect(strip?.text).toBe('› Now add a fog mode. +2 lines')
  expect(await band.find({ type: 'Button', key: 'drawer:read' })).toBeDefined()
  // one row: the strip and its read button side by side
  const row = (await band.findAll({ type: 'Box' })).find((box: { props: Record<string, unknown> }) => box.props.flexDirection === 'row')
  expect(row?.text).toBe('› Now add a fog mode. +2 lines read')
})

test('scrolled back, the drawer names the prompt behind the first reply on screen', async ($, on) => {
  const { clock } = engine(on)
  const band = await $.ui.mount(BAND)
  await conversation($, clock, { p1: null, r1: { first: 3, last: 9, of: 10 }, p2: { first: 0, last: 2, of: 3 }, r2: null })
  expect((await band.find({ type: 'Button', key: 'drawer:strip' }))?.text).toBe(`› ${FIRST}`)
})

test('hovering reveals the full prompt as a card rising from the last row on screen, with no layout change', async ($, on) => {
  const { clock } = engine(on)
  const rows = await conversation($, clock, { p1: null, r1: { first: 3, last: 9, of: 10 }, p2: { first: 0, last: 2, of: 3 }, r2: null })
  // the last row on screen hosts the card, its bottom on that row's bottom, just above the
  // drawer's row; hidden until the strip's hover group lights it (the surface applies hover
  // styles, which the kit validates with the tree but does not expose)
  const card = (await rows.p2.findAll({ type: 'Box' })).find((box: { props: Record<string, unknown> }) => box.props.display === 'none')
  expect(card?.props.position).toBe('absolute')
  expect(card?.props.bottom).toBe(0)
  // the card shows the prompt behind the first reply on screen
  expect((await rows.p2.find({ type: 'Button', key: 'drawer:card' }))?.text).toBe(FIRST)
  // every other row draws no card
  expect(await rows.r1.find({ type: 'Button', key: 'drawer:card' })).toBeUndefined()
  expect(await rows.p1.find({ type: 'Button', key: 'drawer:card' })).toBeUndefined()
})

test('the card moves with the scroll: the new last row hosts it, the old one lets it go', async ($, on) => {
  const { clock } = engine(on)
  const rows = await conversation($, clock, { p1: null, r1: { first: 3, last: 9, of: 10 }, p2: { first: 0, last: 2, of: 3 }, r2: null })
  await rows.r1.unmount()
  await $.ui.mount(reply('r1', 'The timer reads the tide table.', null))
  await rows.r2.unmount()
  const r2 = await $.ui.mount(reply('r2', 'Fog mode blinks twice.', { first: 0, last: 1, of: 2 }))
  await clock.advance(10)
  expect(await rows.p2.find({ type: 'Button', key: 'drawer:card' })).toBeUndefined()
  // the first row on screen is now the second prompt: the card shows it whole
  expect((await r2.find({ type: 'Button', key: 'drawer:card' }))?.text).toBe(SECOND)
})

test('clicking the strip or the card jumps to the prompt', async ($, on) => {
  const { clock, toasts } = engine(on)
  const band = await $.ui.mount(BAND)
  const rows = await conversation($, clock, { p1: null, r1: { first: 3, last: 9, of: 10 }, p2: { first: 0, last: 2, of: 3 }, r2: null })
  // The test kit has no transcript to scroll: the engine's refusal shows the jump was asked for.
  await band.press({ key: 'drawer:strip' })
  await rows.p2.press({ key: 'drawer:card' })
  expect(toasts).toEqual([
    'Could not jump to the prompt: HooksError: no implementation for ui.scroll',
    'Could not jump to the prompt: HooksError: no implementation for ui.scroll',
  ])
})

test('keyboard: /context-drawer opens the whole prompt in a focused pane; Esc closes it, j or Enter jumps', async ($, on) => {
  const { clock, toasts } = engine(on)
  const opened: unknown[] = []
  on('ui.open', (_: unknown, e: unknown) => {
    opened.push(e)
    return { value: { isPlaced: true } }
  })
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  on('process.run', () => ran(1))
  await $.session.start({ cwd: '/home/example' } as never)
  await conversation($, clock)
  const result = await $.command.run({ command: 'context-drawer' } as never)
  expect((result as { text?: string }).text).toMatch(/arrows scroll, Enter jumps, Esc closes/)
  expect(opened).toContainEqual(expect.objectContaining({ id: 'context-drawer', focus: true, closeOnEscape: true }))

  const pane = await $.ui.mount(DRAWER_PANE)
  expect((await pane.find({ type: 'Text', text: /blink twice/ }))?.text).toBe(SECOND)
  const jump = await pane.find({ type: 'Button', key: 'drawer:jump' })
  expect(jump?.props.autoFocus).toBe(true)
  expect(jump?.props.hotkey).toBe('j')
  await pane.press({ key: 'drawer:jump' })
  expect(toasts).toEqual(['Could not jump to the prompt: HooksError: no implementation for ui.scroll'])
})

test('only prompts the person sent are drawer prompts; notifications, other agents and slash commands are not', async ($, on) => {
  const { clock } = engine(on)
  const band = await $.ui.mount(BAND)
  await $.ui.mount(prompt('p1', FIRST))
  await $.ui.mount({ ...prompt('n1', 'Background task finished.'), props: { text: 'Background task finished.', origin: { kind: 'task-notification' }, isExpanded: false } } as never)
  await $.ui.mount({ ...prompt('m1', 'A message from a peer.'), props: { text: 'A message from a peer.', origin: { kind: 'peer' }, isExpanded: false } } as never)
  await $.ui.mount(prompt('c1', '/context-drawer'))
  await $.ui.mount(prompt('c2', '/model fast'))
  await clock.advance(10)
  expect((await band.find({ type: 'Button', key: 'drawer:strip' }))?.text).toBe(`› ${FIRST}`)
})

test('the band yields to a survey and to a subagent view', async ($, on) => {
  const { clock } = engine(on)
  await conversation($, clock)
  const survey = await $.ui.mount({ ...BAND, props: { ...BAND.props, hasSurvey: true } })
  expect(await survey.find({ type: 'Button', key: 'drawer:strip' })).toBeUndefined()
  await survey.unmount()
  const agent = await $.ui.mount({ ...BAND, props: { ...BAND.props, view: { agentId: 'helper-1' } } })
  expect(await agent.find({ type: 'Button', key: 'drawer:strip' })).toBeUndefined()
})

test('each feature switches on its own: the drawer alone draws no info pane', { options: { infoPane: false } }, async ($, on) => {
  const { clock } = engine(on)
  const band = await $.ui.mount(BAND)
  const rows = await conversation($, clock)
  expect(await band.find({ type: 'Button', key: 'drawer:strip' })).toBeDefined()
  // no pin column on replies: that is the info pane's
  expect(await rows.r2.find({ type: 'Button', key: 'pin:r2' })).toBeUndefined()
})

test('the info pane alone draws no drawer row', { options: { contextDrawer: false } }, async ($, on) => {
  const { clock } = engine(on)
  const band = await $.ui.mount(BAND)
  const rows = await conversation($, clock)
  expect(await band.find({ type: 'Button', key: 'drawer:strip' })).toBeUndefined()
  expect(await rows.r2.find({ type: 'Button', key: 'pin:r2' })).toBeDefined()
})

test('a tool call or notice after the last reply hosts the card, so it still rises from the drawer row', async ($, on) => {
  const { clock } = engine(on)
  on('ui.render', { component: 'ToolUse' }, async ($: any, e: any) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{`${e.props.tool} ran`}</Text>
  })
  const rows = await conversation($, clock, { p1: null, r1: null, p2: { first: 0, last: 3, of: 4 }, r2: { first: 0, last: 1, of: 2 } })
  const tool = await $.ui.mount({
    plugin: 'info-pane',
    surface: 'terminal',
    component: 'ToolUse',
    requestId: 'toolu_1',
    props: { tool_use_id: 'toolu_1', tool: 'Read', input: {}, isRunning: false, isErrored: false, isInterrupted: false, onScreen: { first: 0, last: 0, of: 1 } },
    viewport: { columns: 120, rows: 40, isFullscreen: true },
  } as never)
  await clock.advance(10)
  expect(await rows.r2.find({ type: 'Button', key: 'drawer:card' })).toBeUndefined()
  // the tool row hosts it; the context is still the prompt behind the first row on screen
  expect((await tool.find({ type: 'Button', key: 'drawer:card' }))?.text).toBe(SECOND)
})
