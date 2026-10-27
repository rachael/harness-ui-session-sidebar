import { expect, mock, test } from 'claude-code/testing'

import { PANE, reply } from './kit'

const LONG = Array.from({ length: 80 }, (_, i) => `word${i}`).join(' ')

async function setup($: any, on: any) {
  const clock = mock.clock(on)
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.render', { component: 'AssistantMessage' }, async ($: any, e: any) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })
  await $.ui.mount(reply('r1', '## Plan\n\nThe beacon needs a timer.'))
  await $.ui.mount(reply('r2', '```\ncode first\n```\nTide table parsed. ' + LONG))
  await clock.advance(10)
  const ui = await $.ui.mount(PANE)
  await ui.press({ key: 'tab:responses' })
  return { clock, toasts, ui }
}

test('the Responses tab lists each reply on one line, newest first, by its first line', async ($, on) => {
  const { ui } = await setup($, on)
  expect((await ui.find({ type: 'Button', key: 'tab:responses' }))?.text).toBe('Responses 2')
  const rows = (await ui.findAll({ type: 'Button' })).filter((b: { key?: string }) => b.key?.startsWith('jump:'))
  expect(rows.map((b: { key?: string; text?: string }) => [b.key, b.text])).toEqual([
    ['jump:r2', `${('Tide table parsed. ' + LONG).slice(0, 39)}…`],
    ['jump:r1', 'Plan'],
  ])
  // one row each: a long title is cut to the pane (44 columns, less the toggle) and never wraps
  const title = await ui.find({ type: 'Text', text: /^Tide table parsed/ })
  expect([...(title?.text ?? '')].length).toBe(40)
  expect(title?.props.wrap).toBe('truncate-end')
  // no summary until asked
  expect(await ui.find({ type: 'Text', text: /Summary:/ })).toBeUndefined()
})

test('[+] expands a short summary without jumping; [-] collapses it', async ($, on) => {
  const { ui, toasts } = await setup($, on)
  await ui.press({ key: 'toggle:r2' })
  expect((await ui.find({ type: 'Button', key: 'toggle:r2' }))?.text).toBe('[-]')
  const summary = (await ui.findAll({ type: 'Text' })).filter((t: { text: string }) => /^ {2}\S/.test(t.text))
  expect(summary[0]?.text).toMatch(/^ {2}Summary: code first Tide table parsed/)
  expect(summary.length).toBeLessThanOrEqual(4)
  expect(summary.at(-1)?.text.endsWith('…')).toBe(true)
  // expanding is not a jump
  expect(toasts).toEqual([])
  await ui.press({ key: 'toggle:r2' })
  expect(await ui.find({ type: 'Text', text: /Summary:/ })).toBeUndefined()
})

test('a vertical list never expands on hover: no row reveals anything on hover', async ($, on) => {
  const { ui } = await setup($, on)
  const boxes = await ui.findAll({ type: 'Box' })
  expect(boxes.some((box: { props: Record<string, unknown> }) => (box.props.hover as { display?: string } | undefined)?.display)).toBe(false)
})

test('pressing a row jumps to its reply; a reply no longer drawn is dropped and the person told', async ($, on) => {
  const { ui, toasts } = await setup($, on)
  await ui.press({ key: 'toggle:r1' })
  // The test kit has no transcript to scroll, so the engine refuses the jump as it would for a
  // reply that is gone: the row leaves, and the expansion of another row survives.
  await ui.press({ key: 'jump:r2' })
  expect(toasts).toEqual(['That reply is no longer in the transcript.'])
  expect(await ui.find({ type: 'Button', key: 'jump:r2' })).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'toggle:r1' }))?.text).toBe('[-]')
  expect((await ui.find({ type: 'Button', key: 'tab:responses' }))?.text).toBe('Responses 1')
})

test("a subagent's replies are not listed as the main conversation's", async ($, on) => {
  const clock = mock.clock(on)
  on('ui.render', { component: 'AssistantMessage' }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })
  on('agent.list', () => ({ value: [] }))
  const agentView = await $.ui.mount({ ...PANE, props: { ...PANE.props, view: { agentId: 'helper-1' } } })
  await $.ui.mount(reply('s1', 'A subagent reply.'))
  await clock.advance(10)
  await agentView.unmount()
  const ui = await $.ui.mount(PANE)
  expect((await ui.find({ type: 'Button', key: 'tab:responses' }))?.text).toBe('Responses 0')
})
