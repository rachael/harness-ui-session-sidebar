# Session sidebar contract

A narrow list of the harness sessions running on this machine, one line each, and a way
to switch to the terminal tab a session runs in. One optional module, off by default,
switched on and off independently of the info pane, the drawer and the status line.
Every harness's sidebar (the Claude Code plugin, a later Codex one, a terminal pane)
reads the same records through the same commands and draws them its own way.

## What a session says about itself

Each session announces itself from its harness's own hook, at session start, and
forgets itself at session end:

```sh
harness-ui sessions announce PROVIDER SESSION [--pid PID]
harness-ui sessions forget PROVIDER SESSION
```

`announce` merges one field, `terminal`, into the session's shared record
(`sessions/PROVIDER/SESSION.json` under the state root of `contracts/overview.md`), under
that record's lock, through the configured publisher when there is one, and reads it
back (`publish_session(merge=True)`, `state publish-session --merge`). Every other field
is kept. Writers that publish whole records keep it with `--retain-field terminal`.
`forget` sets it to `null`, since a merge cannot remove a key; a record whose `terminal`
is not an object is not listed.

```json
"terminal": {
  "schema": 1,
  "pid": 4242, "start": "Mon Mar 2 09:00:00 2026", "tty": "/dev/ttys004",
  "handle": {"kind": "iterm2", "id": "0F0E0D0C-1111-4222-8333-444455556666"},
  "cwd": "/home/example/lighthouse", "announced_at": 1772442000.0
}
```

- `pid` is the harness process: the nearest ancestor of the announcing command that is
  not a shell or launcher wrapper, unless `--pid` names it. `start` is its start time as
  the process table gives it, so a reused pid is not taken for the session.
- `handle` is what the terminal itself names, read from the harness's environment:

| `kind` | From | Identifies |
| --- | --- | --- |
| `tmux` | `TMUX` (socket), `TMUX_PANE` | the pane `%N` on that server |
| `iterm2` | `ITERM_SESSION_ID` after the `:` | the iTerm2 session's `id` (its guid) |
| `apple-terminal` | `TERM_PROGRAM=Apple_Terminal` and the controlling tty | the tab whose `tty` it is, while the harness holds that tty |
| `multiplexer` | `STY`, `ZELLIJ` | nothing switchable yet |
| `unknown` | any other `TERM_PROGRAM` | nothing switchable |

A multiplexer comes first: inside one, the outer terminal's tab is not the session's own.
New terminals are adapters in `codex_ui_mods.terminals` (`register_adapter`).

Nothing here reads a transcript, calls a model or runs in the background.

## Listing

```sh
harness-ui sessions list [--all]
```

prints one JSON object:

```json
{"schema": 1, "sessions": [
  {"provider": "claude", "badge": "✳", "session": "fixture-a", "state": "running",
   "title": "Lighthouse refactor", "cwd": "/home/example/lighthouse", "branch": "main",
   "model": "", "updated_at": 1772442060.0,
   "activity": {"status": "working", "needs_input": false, "updated_at": 1772442055.0},
   "terminal": {"kind": "iterm2", "focusable": true}}
]}
```

- `state` is only what the process table says: `running` (the pid is alive with the
  recorded start time), `ended` (it is not), `unknown` (no process was recorded). It
  says the session exists, never whether it is working.
- `activity` is what the harness's own events last said (below). `badge` is the
  harness's badge as the info pane marks it (`claude` `✳`, `codex` `C`, else the first
  letter; `HARNESS_UI_BADGES` overrides).
- `title`, `cwd`, `branch` and `model` are the record's own; the sidebar never derives a
  title. With no title it shows the folder.
- `updated_at` is when the session's record last changed, for "3m ago".
- Ended sessions are left out unless `--all`; `--hide HARNESS` (repeatable) leaves a
  harness out. Rows are grouped by harness: `claude`, then `codex`, then any other by
  name; newest first within each.

### Activity

The sidebar only reads `activity`; each harness's own event hooks write it into the
session's record by merge (contracts/overview.md, "Activity"):

```json
"activity": {"status": "awaiting_input", "needs_input": true, "updatedAt": 1772442055000, "source": "native-event"}
```

| Record | Listed as |
| --- | --- |
| `status` `working`, `idle`, `awaiting_input` or `unknown`, `updatedAt` a number of ms | that status; `needs_input` only when `true` |
| no `activity`, another status, `updatedAt` missing or not a number | `unknown` |
| `updatedAt` before the harness process's recorded `start` (a resumed session's last run), or a `start` that cannot be read | `unknown` |

`needs_input` comes before the status: an ask can be open while the session works,
and an idle session can still wait on the person.

Producers, from native events only:

- **Claude Code** (the plugin's `activity.ts`): a submitted prompt is `working`; a
  question dialog, a permission prompt or an elicitation is `awaiting_input` with
  `needs_input`; answering it, or a tool finishing, is `working` again; the end of a
  turn (`Stop`, `StopFailure`, `idle_prompt`) is `idle`, keeping `needs_input` if
  something still waits.
- **Codex** (`codex.py` hooks): `SessionStart` is `unknown`; `UserPromptSubmit`,
  `PreToolUse` and `PostToolUse` are `working`; a `request_user_input` call with an open
  ask is `awaiting_input`; `Stop` and `Interrupt` are `idle`. `needs_input` is whether
  any ask in the record is open.
- **Another harness**: write the same field by merge from its own turn-start,
  turn-end and person-prompt events. With no such events it writes nothing, and its
  rows say `unknown`.

## Switching

```sh
harness-ui sessions focus PROVIDER SESSION [--no-switch-tabs] [--no-switch-panes]
```

switches to the session's tab inside the window the request comes from (the caller:
this command's own terminal, the one the person is using the sidebar in), and only to a
tab whose pane is where the caller's is and the same size. All of it is resolved when
the command runs. It prints `{"status": ..., "detail": ...}` and exits 0 for `focused`
or `already`, else 3:

| `status` | Meaning; nothing was focused unless `focused` |
| --- | --- |
| `focused` | switched to the session's tab |
| `already` | the session is the caller's own |
| `missing` | no tab holds the handle any more, or the session never announced one |
| `ended` | the session's process is gone (or its pid now belongs to another) |
| `other-window` | the session is in another window, another terminal app, or another tmux session |
| `geometry` | its pane is elsewhere or another size; or it is another pane of the caller's own tab |
| `disabled` | `switch_tabs` or `switch_panes` is off |
| `caller-missing` | the caller's own tab was not found |
| `unavailable` | the terminal app is not running |
| `denied` | macOS has not allowed this process to automate the terminal |
| `unsupported` | no adapter for this terminal, or a malformed handle |
| `failed` | the terminal answered something else; `detail` says what |

Every refusal is shown to the person as it is, and leaves every tab where it was.

### Per terminal

- **tmux**: the window is the tmux session, a tab is a tmux window. The target pane's
  `pane_left`, `pane_top`, `pane_width` and `pane_height` must equal the caller's; then
  `select-window` and `select-pane`.
- **iTerm2** (AppleScript): the target must be in the caller's window, in another tab,
  with the caller's `columns` and `rows`; then `select` the tab and session. iTerm2's
  scripting dictionary does not report pane positions, so a tab split into panes is
  refused as `geometry`.
- **Terminal.app** (AppleScript): the target tab must be in the caller's window with the
  caller's `number of rows` and `number of columns`; then the tab is `selected`.
  Terminal.app tabs have no panes.

Handles reach AppleScript as `on run argv` arguments, never as script text. The first
switch asks macOS for permission to automate the terminal app (System Settings →
Privacy & Security → Automation).

## Options

| Option | Default | Environment (tmux sidebar) | Claude Code setting | Effect |
| --- | --- | --- | --- | --- |
| `show_claude` | on | `HARNESS_UI_SIDEBAR_SHOW_CLAUDE` | `showClaude` | list Claude Code sessions |
| `show_codex` | on | `HARNESS_UI_SIDEBAR_SHOW_CODEX` | `showCodex` | list Codex sessions |
| `switch_tabs` | on | `HARNESS_UI_SIDEBAR_SWITCH_TABS` | `switchTabs` | switch to another tab of the caller's window |
| `switch_panes` | on | `HARNESS_UI_SIDEBAR_SWITCH_PANES` | `switchPanes` | allow a target tab that is split into panes, when its pane is where the caller's is |

The sidebar module itself stays off until it is chosen. Once it is on, every harness is
listed, so finding a session takes no further step. An environment value of `0`,
`false`, `no` or `off` turns an option off; anything else, or nothing, leaves it on.
Command-line flags (`--no-switch-tabs`, `--hide codex`) come before the environment.
The window and geometry rules are always applied. The options only narrow them.

## Drawing it

- **Grouped by harness**: a heading per harness, its badge then its name (`✳ Claude
  Code`, `C Codex`), in the host's colour for that harness (Claude Code's theme `claude`
  and `suggestion`; the tmux sidebar uses Claude's orange and Codex's cyan where the
  terminal has colours, else bold).
- **Minimal by default**: one line per session, clipped, never wrapped: the activity
  glyph, a marker for the sidebar's own session, the title (or the folder), the age.

| Glyph | Activity | Words in the details |
| --- | --- | --- |
| `●` | `working` | working |
| `○` | `idle` | idle |
| `?` | `needs_input`, or `awaiting_input` | waiting on you (or "working, waiting on you"), drawn bold in the host's permission colour |
| `·` | `unknown` | activity unknown |
- **Details** appear only through a separate control (`[+]`/`[-]` before the row, or its
  key). They add lines under that row: the activity in words (and the process state
  when it is not `running`), folder, branch, terminal kind, and why it cannot be switched to. Hover never expands, moves or resizes anything.
- **Keyboard**: every row and every toggle can be reached and pressed from the keyboard.
  Up/down move between rows, Enter switches, and a separate key expands.
- **Rows keep their place**: a refusal shows on a status line reserved from the first
  frame, so the list does not move.
- **Narrow**: 32 columns by default (`--width`, 16–60), room for a session name with
  its glyph and age. Any other pane beside it keeps its own width.

## Where it sits

- **Claude Code**: plugin panes dock beside the transcript, on the right. A plugin cannot
  ask for the left. The Claude Code sidebar is therefore a narrow pane in that dock. With
  the info pane also open, the two are tabs of one dock, not two columns.
- **A left sidebar in any terminal**: `harness-ui sessions layout [--width 32] -- CMD`
  runs the harness in a tmux pane with `sessions sidebar` in a pane on its left. Inside
  tmux it splits the current pane; outside, it starts a session on the person's own tmux
  server and configuration and attaches. The sidebar names its harness pane (the owner,
  `--owner-pane`) explicitly. Every switch checks first that the owner pane still exists,
  and uses its geometry, not the sidebar's own, as the origin. A switch never changes the
  layout. Keys: up/down (or j/k) move, Enter switches, Space or Right expands, Left
  collapses, r refreshes, q quits. A click on `[+]` expands, and a click on the row
  switches. The process table and tmux are only called from a worker thread, at most
  every 5 seconds or on request; drawing reads the last snapshot. Nothing changes for a
  harness started without `layout`. The options reach the sidebar pane as arguments,
  since a tmux pane does not inherit the caller's environment.
  It needs tmux 3.2 or later (`split-window -e`).

## For another harness (Codex)

A sidebar in another harness needs no library: it runs the commands above and reads
their JSON.
- Announce on session start with the harness's own session id, and forget on exit.
- Poll `sessions list` no more often than every 5 seconds while the sidebar is shown,
  and once on open.
- Switch with `sessions focus`, showing `status` and `detail` from its answer.
- Draw the groups, glyphs and words above from `badge` and `activity`; pass `--hide`
  for a harness its settings switch off.
- The sidebar's own session is the row whose `provider` and `session` match its own.

## Fixtures

Records in tests are fictional (`fixture-a`, `/home/example/...`), as in
`contracts/fixtures/`.
