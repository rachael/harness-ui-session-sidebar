import { expect, mock, test } from 'claude-code/testing'
import { PANE } from './kit'

const pane = (surface: 'terminal' | 'desktop', placement: 'inline' | 'dock' = 'inline') => ({
  ...PANE, surface, props: { ...PANE.props, placement },
}) as const

test('desktop draws the full pane without terminal dimensions or inline fullscreen hint', async $ => {
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Text', text: /docks in fullscreen/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: 'This conversation' })).toBeDefined()
  const outer = (await ui.findAll({ type: 'Box' }))[0]
  expect(outer?.props.width).toBeUndefined()
  expect(outer?.props.minHeight).toBeUndefined()
  const boxes = await ui.findAll({ type: 'Box' })
  expect(boxes.some(b => b.props.flexWrap === 'wrap')).toBe(true)
  expect(boxes.some(b => b.props.minWidth === 18)).toBe(true)
  await ui.press({ key: 'tab:responses' })
  expect(await ui.find({ type: 'Text', text: 'No responses yet' })).toBeDefined()
  expect((await ui.findAll({ type: 'Box' }))[0]?.props.width).toBeUndefined()
  await ui.unmount()
  const docked = await $.ui.mount(pane('desktop', 'dock'))
  expect((await docked.findAll({ type: 'Box' }))[0]?.props.minHeight).toBeUndefined()
})

test('desktop mascot uses four monospace rows with preserved spaces and any row can be clicked', async ($, on) => {
  mock.clock(on)
  const toasts: string[] = []
  on('ui.toast', (_, e) => { toasts.push(e.text); return { value: undefined } })
  const ui = await $.ui.mount(pane('desktop'))
  const rows = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('little-claude:'))
  expect(rows).toHaveLength(4)
  const text = (await ui.findAll({ type: 'Text' })).filter(t => t.props.mono === true && t.text !== '/')
  expect(text).toHaveLength(4)
  expect(text.every(t => !t.text.includes(' ') && !t.text.includes('\n'))).toBe(true)
  expect(text.some(t => t.text.includes('\u00a0'))).toBe(true)
  await ui.press({ key: rows[2]!.key! })
  expect(toasts).toEqual([])
  expect(await ui.find({ type: 'Text', text: '( hi! :3 )' })).toBeUndefined()
})

test('desktop reserves naturally measured speech rows and row focus shows the hint without reflow', async ($, on) => {
  mock.clock(on)
  on('ui.focus', () => ({}))
  const ui = await $.ui.mount({ ...pane('desktop'), props: { ...pane('desktop').props, isFocused: true } })
  const speech = async () => (await ui.findAll({ type: 'Box' })).find(b => b.key === 'speech')
  expect((await speech())?.props.height).toBeUndefined()
  expect((await ui.findAll({ type: 'Text' })).filter(t => t.props.mono !== true && t.text === '\u00a0').length).toBe(2)
  expect((await ui.findAll({ type: 'Box' })).find(b => b.props.display === 'none')?.props.position).toBe('absolute')
  await $.ui.focus({ component: 'Pane', requestId: 'info-pane', plugin: 'info-pane', element: 'little-claude:2', origin: { kind: 'person' } } as never)
  expect((await ui.findAll({ type: 'Box' })).find(b => b.props.display === 'none')).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: '‹ hi! :3 ›' })).toBeDefined()
  expect((await speech())?.props.height).toBeUndefined()
  expect((await ui.findAll({ type: 'Text' })).filter(t => t.props.mono !== true && t.text === '\u00a0').length).toBe(2)
  await $.ui.focus({ component: 'Pane', requestId: 'info-pane', plugin: 'info-pane', element: 'tab:overview', origin: { kind: 'person' } } as never)
  expect((await ui.findAll({ type: 'Box' })).find(b => b.props.display === 'none')).toBeDefined()
  expect((await ui.findAll({ type: 'Text' })).filter(t => t.props.mono !== true && t.text === '\u00a0').length).toBe(2)
})

test('terminal keeps its inline hint, dock cell dimensions and single mascot button', async $ => {
  const inline = await $.ui.mount(pane('terminal'))
  expect(await inline.find({ type: 'Text', text: /docks in fullscreen/ })).toBeDefined()
  await inline.unmount()
  const docked = await $.ui.mount(pane('terminal', 'dock'))
  const outer = (await docked.findAll({ type: 'Box' }))[0]
  expect(outer?.props.width).toBe(44)
  expect(outer?.props.minHeight).toBe(40)
  expect((await docked.findAll({ type: 'Box' })).some(b => b.props.flexWrap === 'wrap')).toBe(false)
  expect((await docked.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('little-claude'))).toHaveLength(1)
})
