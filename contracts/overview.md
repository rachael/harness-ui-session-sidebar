# Overview content contract

One session record, drawn the same way by every harness adapter (the Codex
info-pane patch, the Claude Code plugin under `adapters/claude-code`). Renderers
stay thin: each reads this record and draws it with its own toolkit.

## Where state lives

The state root is the first of `HARNESS_UI_STATE_DIR`, `CODEX_UI_STATE_DIR`,
`$XDG_STATE_HOME/harness-ui-mods`, `~/.local/state/harness-ui-mods`.

| File | Holds |
| --- | --- |
| `sessions/PROVIDER/SESSION.json` | the session record below |
| `sections/SESSION.json` | optional extra sections, keyed by section id |

Either file is the raw object, or a provenance envelope
`{"schema_version": 1, "revision": "...", "data": {...}}` whose `data` is the raw
object. Readers accept both. Adapters never write an envelope or the canonical
file directly: they stage a full record and run
`harness-ui state publish-session PROVIDER SESSION --input-file STAGED
--retain-field surfaced --consume-input`, which merges `surfaced` from the
current record, writes under the session lock (or delegates to the configured
`HARNESS_UI_PUBLISH_COMMAND`) and reads it back. `HARNESS_UI_COMMAND` overrides
the executable. Adapters publish only when the content changed; a timestamp alone
is no change.

## Session record

| Field | Type | Drawn as |
| --- | --- | --- |
| `harness` | string | header line, `Claude Code · MODEL` |
| `model`, `cwd`, `branch` | string | header lines; `cwd` home-relative |
| `title` | string | heading; `main conversation` when empty |
| `recap` | string | wrapped under the header |
| `pins` | `[{id, text, at}]` | **Pinned**, one clipped line each |
| `views.main.answer` | string | **Last answer**, about a third of the pane, then a "more" control |
| `views.main.asks` | `[{id, text, from, summary?, detail?}]` | **Waiting on**; `summary` over `text`; `from: agent` is background work |
| `views.main.tasks` | `[{id, subject, status, active?, detail?}]` | **In progress**, **Not started**, **Done** (last 12) |
| `views.AGENT` | as `views.main` | the same, for a subagent in view |
| `surfaced` | `[Surfaced]` | **Asks** and **Notes**, below |
| `usage` | `{schema, startedAt, since, input, output, cacheRead, cacheWrite, updated}` | the status line's session totals (contracts/statusline.md) |
| `terminal` | `{pid, start, tty, handle, cwd, announced_at}` | where the session runs, for the session sidebar |
| `activity` | `{status, needs_input, updatedAt, source}` | working or waiting, below |

Unknown fields are kept and ignored. Each field has one writer. The overview writer
publishes the whole record and keeps `surfaced`, `usage`, `terminal` and `activity` from
the current one (`--retain-field`); a writer that owns only some fields merges them
(`harness-ui state publish-session --merge`), under the same lock, publisher and
read-back.

## Activity

`status` is `working`, `idle`, `awaiting_input` or `unknown`; `needs_input` says
something waits on the person; `updatedAt` is the event's time in milliseconds;
`source` is `native-event`. Both come from the harness's own events, never from a
process being alive: before the first event the status is `unknown`. Claude Code: a
submitted prompt is `working`; a question dialog or a permission or elicitation prompt
is `awaiting_input` with `needs_input`; answering it, or a tool finishing, is `working`
again; the end of a turn is `idle`, keeping `needs_input` if something still waits.

## Surfaced content

Written only by the `surface` control (`harness-ui surface ask|note|decision|resolve`),
never by a renderer. Each entry:

```json
{"id": "stable-id", "kind": "ask", "text": "…", "source_anchor": "message:42",
 "surfaced_by": "claude", "badge": "✳", "verified": true, "status": "open"}
```

Renderers:

- draw an entry only when `verified` is `true` and `status` is not `resolved`;
  an unverified entry is not drawn at all, so nothing claims to be surfaced before
  its read-back;
- group `kind: ask` under **Asks**, `note` and `decision` under **Notes**
  (a decision reads `Decision: TEXT`);
- mark each with its badge: `badge` when it is 1–8 printable characters, else the
  provider default (`claude` `✳`, `codex` `C`), else the first letter of
  `surfaced_by` upper-cased; `HARNESS_UI_BADGES` (a JSON object) overrides the
  defaults;
- hide an automatically captured ask whose text matches an open surfaced ask
  (case and punctuation ignored), so the pane does not show it twice.

## Responses list

A second tab lists the session's assistant replies, newest first, one row each:

- title: the reply's first non-empty line, trimmed, clipped to the row;
- `[+]` before it expands a short summary under the row (`Summary: ` and the
  reply's text with whitespace collapsed, at most 4 wrapped lines); `[-]`
  collapses it. Only the explicit control expands; hover never does;
- pressing the row (not the toggle) jumps the transcript to that reply;
- a reply no longer in the transcript is dropped from the list and the jump is
  refused visibly.

Both adapters build this list from their own transcript, not from the record.

## Fixtures

`contracts/fixtures/` holds fictional records. Each adapter's tests load the same
record and assert what it draws: `overview.json` (the record),
`sections.json` (extra sections) and `expected.json` (what must and must not
be visible).
