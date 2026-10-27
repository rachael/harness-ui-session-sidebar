import { expect, mock, test } from 'claude-code/testing'

import { model, PANE, reply } from './kit'

const ANSWER = 'Built the timer.\n\n```\nnot this?\n```\nShould the beacon dim at dawn?'

const answer = ($: any) => $.turn.complete({ answer: ANSWER, durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })

test('tasks land in their sections; a question in the answer waits until answered', async ($, on) => {
  const clock = mock.clock(on)
  let ids = 6
  on('tool.call', { tool: 'TaskCreate' }, (_, e) => ({ result: { task: { id: String(++ids), subject: e.subject } } }))
  on('tool.call', { tool: 'TaskUpdate' }, (_, e) => ({ result: { success: true, taskId: e.taskId, updatedFields: ['status'] } }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('prompt.submit', (_, e) => e)
  on('model.complete', model({ title: 'Beacon timer', recap: 'Building the beacon timer.', settled: [], items: [] }))

  await $.tool.call({ tool: 'TaskCreate', subject: 'Write the timer', description: 'first draft' })
  await $.tool.call({ tool: 'TaskCreate', subject: 'Fog mode', description: 'later' })
  await $.tool.call({ tool: 'TaskUpdate', taskId: '7', status: 'in_progress', activeForm: 'Writing the timer' })
  await answer($)
  await clock.advance(10)

  const ui = await $.ui.mount(PANE)
  const row = async () => (await ui.find({ type: 'Text', text: /^[·✢✳✶✻✽] Write the timer$/ }))?.text
  const first = await row()
  expect(first).toBeDefined()
  // the spark animates while the task is in progress, like the main window's spinner
  await clock.advance(150)
  expect(await row()).not.toBe(first)
  expect(await ui.find({ type: 'Text', text: /^ +Writing the timer$/ })).toBeDefined()
  expect((await ui.find({ type: 'Text', text: /^○ / }))?.text).toBe('○ Fog mode')
  expect(await ui.find({ type: 'Text', text: /^Should the beacon dim at dawn\?$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /not this/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^Done/ })).toBeUndefined()

  // the person answers; the small model says question 1 is settled; Waiting on goes away
  await $.prompt.submit({ text: 'Yes, dim it.' } as never)
  await clock.advance(10)
  expect(await ui.find({ type: 'Text', text: /^Waiting on/ })).toBeUndefined()
})

test('the recap and title come from the small-model pass; an ask expands to its detail on click', async ($, on) => {
  // a prompt box for the test: a draft with an empty line, and what gets filled
  on('prompt.read', () => ({ value: { text: 'first\n\nlast', cursor: 0 } }) as never)
  let filled: { text: string; cursor?: number } | undefined
  on('prompt.fill', (_: unknown, f: { text: string; cursor?: number }) => {
    filled = f
    return { isFilled: true, text: f.text, cursor: f.cursor ?? f.text.length } as never
  })
  const clock = mock.clock(on)
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('model.complete', model({
    title: 'Beacon placement',
    recap: 'Deciding where the beacon sits.',
    settled: [],
    items: [{ id: 'new', summary: 'Pick a side for the beacon: north or south', detail: 'The north pier is taller; the south pier is closer.' }],
  }))

  await answer($)
  await clock.advance(10)

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: 'Beacon placement' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Deciding where the beacon sits.' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Pick a side for the beacon: north or south' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /taller/ })).toBeUndefined()

  const button = (await ui.findAll({ type: 'Button' })).find((b: { key?: string; text?: string }) => b.key?.startsWith('ask:q:') && /north or south/.test(b.text ?? ''))
  expect(button).toBeDefined()
  await ui.press({ key: button!.key! })
  const detail = (await ui.findAll({ type: 'Button' })).find((b: { key?: string; text?: string }) => /taller/.test(b.text ?? ''))
  expect(detail?.key).toBe(button!.key!.replace(/^ask:/, 'paste:'))
  // a click on the description puts the ask, title then description, in the prompt box
  await ui.press({ key: detail!.key! })
  await clock.settle()
  expect(filled?.text).toBe('first\nPick a side for the beacon: north or south — The north pier is taller; the south pier is closer.\nlast')
  expect(filled?.cursor).toBe(filled!.text.length - '\nlast'.length)
})

test('a reply pins from the transcript and unpins from the pane', async ($, on) => {
  mock.clock(on)
  on('ui.render', { component: 'AssistantMessage' }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })
  const message = await $.ui.mount(reply('m1', 'Use the south pier for the lamp.'))
  expect((await message.find({ type: 'Button', key: 'pin:m1' }))?.text).toBe('◇')
  await message.press({ key: 'pin:m1' })
  expect((await message.find({ type: 'Button', key: 'pin:m1' }))?.text).toBe('◆')

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /^Pinned/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'Use the south pier for the lamp.' })).toBeDefined()
  await ui.press({ key: 'unpin:m1' })
  expect(await ui.find({ type: 'Text', text: /^Pinned/ })).toBeUndefined()
})

test('the last answer shows once there is one and the screen has filled', async ($, on) => {
  const clock = mock.clock(on)
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('model.complete', model({ settled: [], items: [] }))
  on('ui.render', { component: 'AssistantMessage' }, async ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>{e.props.text}</Text>
  })
  await answer($)
  await clock.advance(10)
  const ui = await $.ui.mount(PANE)

  // everything still fits on screen: no Last answer yet
  await $.ui.mount(reply('r1', 'Some earlier reply.', { first: 0, last: 0, of: 1 }))
  await clock.advance(10)
  expect(await ui.find({ type: 'Text', text: /^Last answer/ })).toBeUndefined()

  // a reply has gone off screen: the screen has filled
  await $.ui.mount(reply('r0', 'An older reply.', null))
  await clock.advance(10)
  expect(await ui.find({ type: 'Text', text: /^Last answer/ })).toBeDefined()
})

test('the little Claude answers a click with no model call', async ($, on) => {
  const clock = mock.clock(on)
  let toasts = 0
  let calls = 0
  on('ui.toast', () => {
    toasts++
    return { value: undefined }
  })
  on('model.complete', () => {
    calls++
    return { value: { isAnswered: false, reason: 'empty-reply', usage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 } } }
  })
  const ui = await $.ui.mount(PANE)
  const before = (await ui.find({ type: 'Button', key: 'little-claude' }))?.text
  expect(before).toBeDefined()
  await ui.press({ key: 'little-claude' })
  await clock.advance(400)
  const mid = (await ui.find({ type: 'Button', key: 'little-claude' }))?.text
  await clock.advance(5000)
  const after = (await ui.find({ type: 'Button', key: 'little-claude' }))?.text
  // A wiggle, recap toast or swap is visible; no action calls a model.
  expect(mid !== before || toasts > 0 || after !== before).toBe(true)
  expect(calls).toBe(0)
})

test('the conversation agent lists and closes asks with the asks tool; a click dismisses one', async ($, on) => {
  const clock = mock.clock(on)
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('model.complete', model({
    settled: [],
    items: [
      { id: 'new', summary: 'Approve the brass fittings?' },
      { id: 'new', summary: 'Add a second foghorn?' },
      { id: 'new', summary: 'Keep the spare lens?' },
    ],
  }))
  await answer($)
  await clock.advance(10)

  // the result is JSON text: an MCP-style tool's result must be a string or content blocks
  const call = async (input: object) => {
    const r = (await $.tool.call({ tool: 'mcp__info-pane__asks', ...input } as never)) as { result: unknown }
    expect(typeof r.result).toBe('string')
    return JSON.parse(r.result as string) as { closed: string[]; notClosed?: string[]; open: { id: string; item: string }[] }
  }
  const listed = await call({})
  const idOf = (text: string) => listed.open.find(a => a.item.includes(text))?.id
  expect(idOf('brass')).toBeDefined()

  const closed = await call({ close: [idOf('brass'), idOf('foghorn'), 'no-such-id'] })
  expect(closed.closed).toHaveLength(2)
  expect(closed.notClosed).toEqual(['no-such-id'])

  const ui = await $.ui.mount(PANE)
  expect(await ui.find({ type: 'Text', text: /brass|foghorn/ })).toBeUndefined()
  const keep = (await ui.findAll({ type: 'Button' })).find(b => /spare lens/.test(b.text ?? ''))
  await ui.press({ key: keep!.key! })
  // the ✕ sits in the indent before the description; no separate dismiss row
  expect(await ui.find({ type: 'Text', text: /✕ dismiss/ })).toBeUndefined()
  await ui.press({ key: keep!.key!.replace(/^ask:/, 'dismiss:') })
  expect(await ui.find({ type: 'Text', text: /spare lens/ })).toBeUndefined()
  // the fixture answer's own question is untouched
  expect(await ui.find({ type: 'Text', text: /dim at dawn/ })).toBeDefined()
})

test('an explicit AskUserQuestion waits in the pane while its dialog is open, then leaves', async ($, on) => {
  mock.clock(on)
  let release: () => void = () => undefined
  const answered = new Promise<void>(resolve => (release = resolve))
  let shown: string | undefined
  let options: string | undefined
  let ui: any
  on('tool.call', { tool: 'AskUserQuestion' }, async () => {
    shown = (await ui.find({ type: 'Text', text: /lamp colour/ }))?.text
    const row = (await ui.findAll({ type: 'Button' })).find((b: { key?: string; text?: string }) => /lamp colour/.test(b.text ?? ''))
    await ui.press({ key: row!.key! })
    options = (await ui.findAll({ type: 'Button' })).find((b: { text?: string }) => /Amber/.test(b.text ?? ''))?.text
    await answered
    return { result: { answers: { q: 'Amber' } } } as never
  })
  ui = await $.ui.mount(PANE)
  const call = $.tool.call({
    tool: 'AskUserQuestion',
    questions: [{ question: 'Which lamp colour?', header: 'Lamp', multiSelect: false, options: [{ label: 'Amber', description: 'warm' }, { label: 'White', description: 'bright' }] }],
  } as never)
  release()
  await call
  expect(shown).toBe('Which lamp colour?')
  expect(options).toMatch(/Amber · White/)
  expect(await ui.find({ type: 'Text', text: /lamp colour/ })).toBeUndefined()
})

// Without a session start no Claude has been picked, and the pane draws the first: the waver,
// whose line appears on hover/focus and whose click wiggles.

test('speech and stem share hover visibility; a toast companion click only wiggles without reflow', async ($, on) => {
  const clock = mock.clock(on)
  const toasts: string[] = []
  on('ui.toast', (_: unknown, e: { text: string }) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  const ui = await $.ui.mount(PANE)
  const speech = async () => (await ui.findAll({ type: 'Box' })).find((box: { props: Record<string, unknown> }) => box.props.height === 2)
  // two rows reserved above the Claude whether or not anything is said: nothing moves
  expect((await speech())?.props.flexDirection).toBe('column')
  const parts = (await ui.findAll({ type: 'Box' })).filter(box => box.key === 'speech-bubble' || box.key === 'speech-stem')
  expect(parts).toHaveLength(2)
  expect(parts.every(part => part.props.position === 'absolute' && part.props.display === 'none')).toBe(true)
  expect(parts.map(part => part.text)).toEqual(['‹ hi! :3 ›', '/'])
  // drawn() retains the surface's declarative hover styles; findAll() omits them.
  const nodes = (node: any): any[] => [node, ...(node?.children ?? []).filter((child: unknown) => typeof child === 'object').flatMap(nodes)]
  const drawn = nodes(await ui.drawn())
  for (const key of ['speech-bubble', 'speech-stem']) {
    expect(drawn.find(node => node.props?.key === key)?.hover).toEqual({ scope: 'little-claude', display: 'flex' })
  }
  expect(drawn.find(node => node.props?.key === 'speech-stem')?.props).toMatchObject({ top: 1, left: 8 })
  const spriteContainer = drawn.find(node => node.children?.some((child: any) => child.props?.key === 'speech-stem'))
  // Empty sprite cells still paint: the stem must be drawn after the button/art.
  expect(spriteContainer?.children.at(-1)?.props?.key).toBe('speech-stem')
  const before = (await ui.find({ type: 'Button', key: 'little-claude' }))?.text
  expect(before).not.toContain('/')

  await ui.press({ key: 'little-claude' })
  await clock.advance(400)
  expect((await ui.find({ type: 'Button', key: 'little-claude' }))?.text).not.toBe(before)
  expect(toasts).toEqual([])
  expect((await speech())?.props.height).toBe(2)
  await clock.advance(4000)
  expect((await ui.find({ type: 'Button', key: 'little-claude' }))?.text).toBe(before)
  expect((await speech())?.props.height).toBe(2)
})

test('keyboard focus reveals bubble and stem together and blur hides both without reflow', async ($, on) => {
  mock.clock(on)
  on('ui.focus', () => ({}))
  const ui = await $.ui.mount({ ...PANE, props: { ...PANE.props, isFocused: true } })
  const parts = async () => (await ui.findAll({ type: 'Box' })).filter(box => box.key === 'speech-bubble' || box.key === 'speech-stem')
  expect((await parts()).every(part => part.props.display === 'none')).toBe(true)
  // the person tabs onto it: the engine raises ui.focus for the element taking the ring
  await $.ui.focus({ component: 'Pane', requestId: 'info-pane', plugin: 'info-pane', element: 'little-claude', origin: { kind: 'person' } } as never)
  expect((await parts()).every(part => part.props.display === 'flex')).toBe(true)
  expect(await ui.find({ type: 'Text', text: '‹ hi! :3 ›' })).toBeDefined()
  await $.ui.focus({ component: 'Pane', requestId: 'info-pane', plugin: 'info-pane', element: 'tab:overview', origin: { kind: 'person' } } as never)
  expect((await parts()).every(part => part.props.display === 'none')).toBe(true)
  expect((await ui.findAll({ type: 'Box' })).find(box => box.key === 'speech')?.props.height).toBe(2)
})
