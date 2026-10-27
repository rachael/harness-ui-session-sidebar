// What the plugin keeps per session. A conversation is the main loop ("main") or one
// subagent, by its agentId: the pane draws the one in view.

export type TaskStatus = 'pending' | 'in_progress' | 'completed'

export type Task = {
  id: string
  subject: string
  status: TaskStatus
  // activeForm ("Running tests"): what it's doing while in progress
  active?: string
  // the description's first line: a progress note
  detail?: string
  from: 'task' | 'todo'
}

// Something the agent waits on: a question it asked, or work running in the background.
// summary: the ask in a line; detail: the options and context behind it, shown on click.
export type Ask = {
  id: string
  text: string
  from: 'dialog' | 'answer' | 'agent'
  summary?: string
  detail?: string
}

export type View = {
  answer: string
  answerAt: number
  asks: Ask[]
  tasks: Task[]
}

export type Harness = {
  model: string
  cwd: string
  branch: string
}

// A reply the person pinned from the transcript; a click in the pane unpins it.
export type Pin = { id: string; text: string; at: number }

// title: the session's own title when the harness gives one, else the small model's.
// recap: a sentence or two on what the conversation is doing and where it stands.
// recapFor: the answerAt of the answer the recap last covered, so a reload can tell a
// recap is owed.
export type Meta = { title: string; recap: string; sessionTitle: string; recapFor?: number }

// The little Claude in the corner: which one, and which frame shows.
export type Corner = { name: string; frame: number }

// One reply in the Responses tab: its transcript id, first line and collapsed text.
export type Response = { id: string; title: string; summary: string }

// Verified, open content from the canonical record's `surfaced` list.
export type SurfacedItem = { id: string; kind: 'ask' | 'note' | 'decision'; text: string; badge: string; by: string }

export type SectionRow = { label: string; value: string }
export type SectionData = { title: string; icon: string; rows: SectionRow[] }

// What other processes wrote for this session, read from the shared state root.
export type External = { surfaced: SurfacedItem[]; sections: SectionData[] }

// A transcript row the context drawer knows: a prompt the person sent, a reply, or any other
// row (a tool call, a command's output, a notice). Only a prompt keeps its text.
export type Row = { id: string; kind: 'prompt' | 'reply' | 'other'; text: string }

// One row of the session sidebar, as `harness-ui sessions list` reports it
// (contracts/session-sidebar.md).
// What the harness's own events last said (contracts/overview.md, `activity`); unknown
// when none did, never inferred from a running process.
export type SidebarActivity = { status: 'working' | 'idle' | 'awaiting_input' | 'unknown'; needsInput: boolean }

export type SidebarSession = {
  provider: string
  badge: string
  session: string
  state: 'running' | 'ended' | 'unknown'
  title: string
  cwd: string
  branch: string
  updatedAt: number
  activity: SidebarActivity
  terminal: string
  isFocusable: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'info-pane': {
      views: Record<string, View>
      harness: Harness
      isAnswerOpen: boolean
      pins: Pin[]
      meta: Meta
      openAsks: string[]
      corner: Corner
      said: string
      mascotFocus: boolean
      // this session's model request totals, for the status line
      sessionTokens: { startedAt: number; since: number; input: number; output: number; cacheRead: number; cacheWrite: number }
      lastPrompt: string
      screenFilled: boolean
      spin: number
      tab: 'overview' | 'responses'
      responses: Response[]
      openResponses: string[]
      external: External
      rows: Row[]
      onScreen: string[]
      drawerFor: string
      // per transcript row: whether it hosts the drawer card
      cardHost: StateFamily<boolean>
      // the session sidebar: the listed sessions, the rows whose details are open, the last switch's outcome
      sidebarSessions: SidebarSession[]
      sidebarExpanded: string[]
      sidebarNote: string
    }
  }
}
