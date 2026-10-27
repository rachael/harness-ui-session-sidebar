// Session sidebar (contracts/session-sidebar.md): a narrow pane listing the harness sessions
// running on this machine, grouped by harness, one line each with what the harness's own
// events last said (working, idle, waiting on you, unknown); pressing one switches to its
// terminal tab, inside this window only. The list and the switch come from the `sessions`
// commands (`sessions list`, `sessions focus`) of the plugin's bundled runtime, or of the
// harness-ui named by HARNESS_UI_COMMAND, which read the shared session records and ask the terminal; this
// module only draws. This session announces itself at start and forgets itself at the end.
//
// Claude Code docks plugin panes beside the transcript, on the right: a plugin cannot ask
// for the left. With the info pane open too, the two are tabs of one dock.
import { atom, read, update } from 'claude-code'
import type { EngineInterface as Engine, Register } from 'claude-code'

import type { SidebarActivity, SidebarSession } from '../types'
import { clipRow } from './responses'
import { PROVIDER, SAFE_ID, command } from './state'

type On = Parameters<Register>[0]
type Options = { switchTabs?: unknown; switchPanes?: unknown; showClaude?: unknown; showCodex?: unknown }

export const SIDEBAR = 'session-sidebar'
const WIDTH = 32
const POLL_MS = 5000
const NAMES: Record<string, string> = { claude: 'Claude Code', codex: 'Codex' }
// Each harness's heading in the host theme's color for it.
const ACCENTS: Record<string, 'claude' | 'suggestion'> = { claude: 'claude', codex: 'suggestion' }
const GLYPHS: Record<SidebarActivity['status'], string> = { working: '●', idle: '○', awaiting_input: '?', unknown: '·' }
const WORDS: Record<SidebarActivity['status'], string> = { working: 'working', idle: 'idle', awaiting_input: 'waiting on you', unknown: 'activity unknown' }

const listed = atom({ plugin: 'info-pane', key: 'sidebarSessions' } as const, [])
const expanded = atom({ plugin: 'info-pane', key: 'sidebarExpanded' } as const, [])
// The outcome of the last switch, on a row kept from the first frame.
const note = atom({ plugin: 'info-pane', key: 'sidebarNote' } as const, '')

let poller: { cancel: () => void } | undefined
let self = ''
let chosen: Options = {}

const keyOf = (row: { provider: string; session: string }) => `${row.provider}:${row.session}`


async function cli($: Engine, args: string[]): Promise<{ exitCode: number; value: unknown }> {
  try {
    const out = await $.process.run([...(await command($)), 'sessions', ...args], { timeoutMs: 15000 })
    let value: unknown
    try {
      value = JSON.parse(out.stdout)
    } catch {
      value = undefined
    }
    return { exitCode: out.exitCode, value }
  } catch {
    return { exitCode: -1, value: undefined }
  }
}

const str = (value: unknown) => (typeof value === 'string' ? value : '')

// Something waiting on the person comes first: an ask can be open while it works.
export const glyph = (activity: SidebarActivity) => (activity.needsInput ? '?' : GLYPHS[activity.status])
export const words = (activity: SidebarActivity) =>
  activity.needsInput && activity.status !== 'awaiting_input' ? `${WORDS[activity.status]}, waiting on you` : WORDS[activity.status]

function activityOf(value: unknown): SidebarActivity {
  const a = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  const status = a.status === 'working' || a.status === 'idle' || a.status === 'awaiting_input' ? a.status : 'unknown'
  return { status, needsInput: a.needs_input === true }
}

// The harnesses left out: showClaude / showCodex set to false.
export const hidden = (options: Options) => [...(options.showClaude === false ? ['claude'] : []), ...(options.showCodex === false ? ['codex'] : [])]

// What `sessions list` printed, kept to the fields the sidebar draws.
export function parseSessions(value: unknown): SidebarSession[] {
  const rows = value && typeof value === 'object' ? (value as { sessions?: unknown }).sessions : undefined
  if (!Array.isArray(rows)) return []
  return rows.flatMap((row): SidebarSession[] => {
    if (!row || typeof row !== 'object') return []
    const r = row as Record<string, unknown>
    const terminal = (r.terminal && typeof r.terminal === 'object' ? r.terminal : {}) as Record<string, unknown>
    if (!str(r.provider) || !str(r.session)) return []
    return [
      {
        provider: str(r.provider),
        badge: str(r.badge),
        session: str(r.session),
        state: r.state === 'running' || r.state === 'ended' ? r.state : 'unknown',
        title: str(r.title),
        cwd: str(r.cwd),
        branch: str(r.branch),
        updatedAt: typeof r.updated_at === 'number' ? r.updated_at : 0,
        activity: activityOf(r.activity),
        terminal: str(terminal.kind) || 'none',
        isFocusable: terminal.focusable === true,
      },
    ]
  })
}

export function age(seconds: number) {
  if (seconds < 60) return 'now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

const homeRelative = (path: string, home: string) => (home && (path === home || path.startsWith(`${home}/`)) ? `~${path.slice(home.length)}` : path)

export async function refresh($: Engine) {
  const out = await cli($, ['list', ...hidden(chosen).flatMap(harness => ['--hide', harness])])
  if (out.exitCode !== 0) {
    await update($, note, () => 'Sessions unavailable: is python3 (3.11 or later) on PATH?')
    return
  }
  const rows = parseSessions(out.value)
  await update($, listed, () => rows)
}

// What a refusal says, as `sessions focus` reports it.
const SAID: Record<string, string> = {
  focused: '',
  already: 'This is the current session.',
  missing: 'Its tab is gone; nothing switched.',
  ended: 'That session has ended.',
  'other-window': 'It is in another window; nothing switched.',
  geometry: 'Its pane is placed differently; nothing switched.',
  disabled: 'Switching is off for that.',
  'caller-missing': 'Could not find this tab; nothing switched.',
  unavailable: 'Its terminal is not running.',
  denied: 'Allow automation for the terminal (System Settings).',
  unsupported: 'Cannot switch to that terminal.',
}

async function switchTo($: Engine, row: SidebarSession, options: Options) {
  const flags = [options.switchTabs === false ? '--no-switch-tabs' : '--switch-tabs', options.switchPanes === false ? '--no-switch-panes' : '--switch-panes']
  const out = await cli($, ['focus', row.provider, row.session, ...flags])
  const status = str((out.value as { status?: unknown } | undefined)?.status)
  const said = status in SAID ? SAID[status]! : 'Could not switch; nothing else was focused.'
  await update($, note, () => said)
}

function open($: Engine) {
  return $.ui.open({ id: SIDEBAR, title: 'Sessions', columns: WIDTH })
}

export function registerSessionSidebar(on: On, options: Options) {
  chosen = options
  on('command.run', { command: 'sessions' }, async $ => {
    await open($)
    await refresh($)

    return { text: 'Sessions pane opened.' }
  })

  on('session.end', async ($, e, next) => {
    if (self) await cli($, ['forget', PROVIDER, self])

    return next(e)
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: SIDEBAR }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const width = e.props.bodyColumns
    const rows = await read($, listed)
    const open = await read($, expanded)
    const said = await read($, note)
    const home = ((await $.env.get('HOME').catch(() => undefined)) ?? '').trim()
    const now = (await $.clock.now()) / 1000

    return (
      <Box flexDirection="column" width={width}>
        <Box flexDirection="row" gap={1}>
          <Text bold color="claude">
            Sessions
          </Text>
          <Button key="sidebar:refresh" plain hotkey="r" dimColor label="refresh" onPress={() => void refresh($)} />
        </Box>
        {/* Kept from the first frame, a space when there is nothing to say, so the list never moves. */}
        <Text dimColor wrap="truncate-end">
          {said || ' '}
        </Text>
        {rows.length === 0 ? <Text dimColor>No sessions announced</Text> : null}
        {rows.map((row, index) => {
          const id = keyOf(row)
          const isOpen = open.includes(id)
          const isSelf = row.provider === PROVIDER && row.session === self
          const mark = glyph(row.activity)
          const when = ` ${age(Math.max(0, now - row.updatedAt))}`
          const hotkey = index < 9 ? String(index + 1) : undefined
          // One line: the label gets what the toggle, the hotkey, the mark and the age leave.
          const room = width - 3 - (hotkey ? 3 : 0) - 2 - (isSelf ? 2 : 0) - when.length
          const label = clipRow(row.title || homeRelative(row.cwd, home) || row.session, room)
          const isGroupStart = index === 0 || rows[index - 1]!.provider !== row.provider
          return (
            <Box key={`sidebar:${id}`} flexDirection="column">
              {isGroupStart ? (
                <Text bold color={ACCENTS[row.provider]} wrap="truncate-end">{`${row.badge || '•'} ${NAMES[row.provider] ?? row.provider}`}</Text>
              ) : null}
              <Box flexDirection="row">
                <Button
                  key={`sidebar:toggle:${id}`}
                  plain
                  label={isOpen ? '[-]' : '[+]'}
                  onPress={() => update($, expanded, list => (list.includes(id) ? list.filter(one => one !== id) : [...list, id]))}
                />
                <Box flexGrow={1} flexShrink={1}>
                  <Button
                    key={`sidebar:row:${id}`}
                    plain
                    hotkey={hotkey}
                    dimColor={!row.isFocusable && !isSelf}
                    onPress={() => void switchTo($, row, options)}
                  >
                    <Text wrap="truncate-end">
                      <Text bold={mark === '?'} color={mark === '?' ? 'permission' : undefined}>{mark}</Text>
                      {` ${isSelf ? '› ' : ''}${label}`}
                    </Text>
                  </Button>
                </Box>
                <Text dimColor>{when}</Text>
              </Box>
              {isOpen ? (
                <Box flexDirection="column" paddingLeft={4}>
                  <Text dimColor wrap="truncate-end">{words(row.activity) + (row.state === 'running' ? '' : ` · process ${row.state}`)}</Text>
                  {row.cwd ? <Text dimColor wrap="truncate-end">{homeRelative(row.cwd, home) + (row.branch ? ` (${row.branch})` : '')}</Text> : null}
                  <Text dimColor wrap="truncate-end">{row.isFocusable || isSelf ? row.terminal : `${row.terminal}: cannot switch`}</Text>
                </Box>
              ) : null}
            </Box>
          )
        })}
      </Box>
    )
  })
}

// Run from the plugin's one session.start hook.
export async function startSessionSidebar($: Engine) {
  self = await $.session.id().catch(() => '')
  await $.command.register({ name: 'sessions', description: 'Open the session sidebar: the sessions running here, and a switch to their tabs' })
  if (SAFE_ID.test(self)) await cli($, ['announce', PROVIDER, self])
  await refresh($)
  // Unasked, the engine places it from 144 columns; /sessions places it at any width.
  void open($).catch(() => undefined)
  poller?.cancel()
  // Polled only while the pane is drawn; a hidden pane costs nothing.
  poller = $.clock.every(POLL_MS, () => {
    void (async () => {
      const panes = await $.ui.panes().catch(() => [])
      if (panes.some(pane => pane.id === SIDEBAR && pane.isPlaced && pane.isShown)) await refresh($)
    })()
  })
}
