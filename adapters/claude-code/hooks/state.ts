// Shared harness UI state: where it lives, how a record is read, and how one is published.
// The plugin never writes the canonical record itself. It stages a full record and asks the
// harness-ui CLI to publish it, which merges `surfaced`, takes the session lock (or delegates
// to the configured publisher) and reads it back. See contracts/overview.md.
import type { EngineInterface as Engine } from 'claude-code'

export const PROVIDER = 'claude'
// What a session or section id may be, as the CLI accepts it.
export const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/

const text = (value: string | undefined) => (value ?? '').trim()

// Where this session's state lives, and the badges configured for surfaced content. The
// harness-ui CLI decides (`state paths`): explicit environment, then an enabled data module's
// settings, then the default. Without the CLI the plugin reads the environment itself.
export type Settings = { root: string; badges: string }

// The lookup in flight or done, shared so concurrent callers start one process.
let resolved: { settings: Promise<Settings | undefined>; at: number } | undefined
// A failed lookup is retried after this long, so a CLI installed later is found.
const RETRY_MS = 60_000

export async function settings($: Engine): Promise<Settings> {
  const now = await $.clock.now()
  if (!resolved || ((await resolved.settings) === undefined && now - resolved.at >= RETRY_MS)) {
    resolved = { settings: fromCli($), at: now }
  }
  return (await resolved.settings) ?? fromEnvironment($)
}

export async function stateRoot($: Engine): Promise<string> {
  return (await settings($)).root
}

async function fromCli($: Engine): Promise<Settings | undefined> {
  try {
    const out = await $.process.run([...(await command($)), 'state', 'paths', '--provider', PROVIDER], { timeoutMs: 5000 })
    if (out.exitCode !== 0) return undefined
    const value = JSON.parse(out.stdout) as { state_root?: unknown; environment?: Record<string, unknown> }
    const root = value.state_root
    if (typeof root !== 'string' || !root.startsWith('/')) return undefined
    const badges = value.environment?.HARNESS_UI_BADGES
    return { root: root.replace(/\/+$/, ''), badges: typeof badges === 'string' ? badges : '' }
  } catch {
    return undefined
  }
}

// HARNESS_UI_STATE_DIR, then CODEX_UI_STATE_DIR, then $XDG_STATE_HOME/harness-ui-mods,
// then ~/.local/state/harness-ui-mods.
async function fromEnvironment($: Engine): Promise<Settings> {
  const home = text(await $.env.get('HOME').catch(() => undefined))
  const expand = (path: string) => (path === '~' ? home : path.startsWith('~/') ? `${home}${path.slice(1)}` : path)
  const badges = text(await $.env.get('HARNESS_UI_BADGES').catch(() => undefined))
  const explicit =
    text(await $.env.get('HARNESS_UI_STATE_DIR').catch(() => undefined)) || text(await $.env.get('CODEX_UI_STATE_DIR').catch(() => undefined))
  if (explicit) return { root: expand(explicit).replace(/\/+$/, ''), badges }
  const xdg = text(await $.env.get('XDG_STATE_HOME').catch(() => undefined))
  return { root: `${xdg ? expand(xdg).replace(/\/+$/, '') : `${home}/.local/state`}/harness-ui-mods`, badges }
}

// The state commands: HARNESS_UI_COMMAND names a harness-ui executable to use (an absolute
// path or a name on PATH); otherwise the plugin's own bundled copy runs, under python3, so an
// install needs nothing else.
export async function command($: Engine): Promise<string[]> {
  const named = text(await $.env.get('HARNESS_UI_COMMAND').catch(() => undefined))
  return named ? [named] : ['python3', `${$.plugin.root}/runtime/harness-ui-state.py`]
}

export const sessionFile = (root: string, session: string) => `${root}/sessions/${PROVIDER}/${session}.json`
export const sectionsFile = (root: string, id: string) => `${root}/sections/${id}.json`
// One staging file per session: a failed publication leaves at most this one behind, and the
// next attempt replaces it.
export const stagingFile = (root: string, session: string) => `${root}/staging/${PROVIDER}-${session}.json`

// A raw object, or a schema-1 provenance envelope whose `data` is the raw object.
export function unwrap(value: unknown): Record<string, unknown> | undefined {
  const inner = value && typeof value === 'object' && (value as { schema_version?: unknown }).schema_version === 1 ? (value as { data?: unknown }).data : value
  return inner && typeof inner === 'object' && !Array.isArray(inner) ? (inner as Record<string, unknown>) : undefined
}

export async function readRecord($: Engine, path: string): Promise<Record<string, unknown> | undefined> {
  try {
    const text = await $.fs.read(path)
    return text.length > 1_000_000 ? undefined : unwrap(JSON.parse(text))
  } catch {
    return undefined
  }
}

// Key order never changes the key, so a re-serialized record with the same content matches.
export const contentKey = (value: unknown): string =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item,
  )

// Fields of the session record other writers own (the surface control, the status line
// collector, the session sidebar, the activity producer): the overview publication keeps them.
export const OTHER_FIELDS = ['surfaced', 'usage', 'terminal', 'activity'] as const

export async function publishArgv($: Engine, session: string, input: string): Promise<string[]> {
  const retain = OTHER_FIELDS.flatMap(field => ['--retain-field', field])
  return [...(await command($)), 'state', 'publish-session', PROVIDER, session, '--input-file', input, ...retain, '--consume-input']
}

export async function publish($: Engine, session: string, record: object): Promise<boolean> {
  if (!SAFE_ID.test(session)) return false
  const root = await stateRoot($)
  const input = stagingFile(root, session)
  await $.fs.write(input, JSON.stringify(record))
  const result = await $.process.run(await publishArgv($, session, input), { timeoutMs: 15000 })
  return result.exitCode === 0
}

// A writer that owns a few top-level fields merges them into the record under the
// publisher's lock, leaving every other field as it is. `kind` names its staging file,
// one per writer and session, so writers never share one.
export async function publishFields($: Engine, session: string, kind: string, fields: Record<string, unknown>): Promise<boolean> {
  if (!SAFE_ID.test(session) || !/^[a-z]+$/.test(kind)) return false
  const input = `${await stateRoot($)}/staging/${PROVIDER}-${session}.${kind}.json`
  await $.fs.write(input, JSON.stringify({ session, ...fields }))
  const argv = [...(await command($)), 'state', 'publish-session', PROVIDER, session, '--input-file', input, '--merge', '--consume-input']
  const result = await $.process.run(argv, { timeoutMs: 15000 })
  return result.exitCode === 0
}
