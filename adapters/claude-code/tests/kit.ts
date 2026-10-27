// What the tests share: the pane as the engine mounts it, and a stand-in session start.
export const PANE = {
  plugin: 'info-pane',
  surface: 'terminal',
  component: 'Pane',
  requestId: 'info-pane',
  props: { title: 'Info pane', isFocused: false, bodyColumns: 44, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
  viewport: { columns: 200, rows: 50, isFullscreen: true },
} as const

export const USAGE = { input_tokens: 1, output_tokens: 1, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }

// The model beneath: the settle call (40 tokens) answers "1"; the refine call answers `refined`.
// No test reaches a real model.
export const model = (refined: object) => (_: unknown, e: { maxTokens?: number }) => ({
  value: { isAnswered: true as const, text: e.maxTokens === 40 ? '1' : JSON.stringify(refined), usage: USAGE },
})

export type Seen = { first: number; last: number; of: number } | null

export const reply = (requestId: string, text: string, onScreen?: Seen) =>
  ({
    plugin: 'info-pane',
    surface: 'terminal',
    component: 'AssistantMessage',
    requestId,
    props: { text, isFirstOfReply: true, ...(onScreen === undefined ? {} : { onScreen }) },
    viewport: { columns: 120, rows: 40, isFullscreen: true },
  }) as const

export const prompt = (requestId: string, text: string, onScreen?: Seen) =>
  ({
    plugin: 'info-pane',
    surface: 'terminal',
    component: 'UserMessage',
    requestId,
    props: { text, origin: { kind: 'composer' as const }, isExpanded: false, ...(onScreen === undefined ? {} : { onScreen }) },
    viewport: { columns: 120, rows: 40, isFullscreen: true },
  }) as const

// A host command's result as the engine answers it.
export const ran = (exitCode: number, stdout = '') => ({ value: { exitCode, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })
