# Status line contract

One status line, laid out the same way by every harness: the Codex status line
(`status_hud.rs`) and the Python renderer for Claude Code's native `statusLine` slot
(`codex_ui_mods.statusline`). Each reads only what its harness reports natively, in
process: no transcript scans, no model calls, no prices of its own.
`contracts/fixtures/statusline.json` holds golden lines at several widths.

## Items

Configured by `HARNESS_UI_STATUS_LINE_ITEMS` (comma-separated order and selection;
unknown names ignored). Default `project,model,effort,tier,context,limits,session,cache`;
`cost` is opt-in. Groups are separated by ` ‖ `, items within a group by ` · `
(`HARNESS_UI_STATUS_LINE_ASCII=1`: ` || `, ` | `, `~`). Colors per item, on unless
`HARNESS_UI_STATUS_LINE_COLORS=0` (or `NO_COLOR`); `=1` forces them on.

| Item | Example | Meaning | Drop priority |
| --- | --- | --- | --- |
| project | `~/proj (main*)` | folder, home-relative; branch, `*` when dirty | kept, truncated in the middle |
| model | `Opus 5.5` | the model answering | 3 |
| effort | `high` | reasoning effort | 9 |
| tier | `priority` | service tier, only where the harness reports one (Codex) | 9 |
| context | `ctx 412K/1M 41%` | the window now: tokens in it / its size, percent; 70% yellow, 90% red | never dropped; last shortens to `ctx 41%` |
| limits | `5h 66% left reset 14:20` | each rate-limit window as what is left; reset later today as a time, else a weekday | 4, then 5 |
| session | `sess 1.28M uncached+out` | this session's uncached input plus output | 6 |
| cache | `cache read 92%` | cache reads over all input, same session | 7 |
| cost | `$1.23` | the harness's own cost ledger | 8 |

Narrower than the line: the highest priority number goes first; then context shortens
and project truncates; if still too wide, project goes. Never a second line.

## Readings

- No reading (not reported, or not yet): the item is left out.
- A reading of zero is shown: `ctx 0/200K 0%`, `5h 0% left`, `sess 0 uncached+out`,
  `$0.00`. Cache read is left out while there is no input to divide by.
- Partial counts (a resume or restart started counting after the session began) read
  `since HH:MM 120K uncached+out`.

## Token semantics

| Field | Claude Code | Codex |
| --- | --- | --- |
| uncached input | `input_tokens` (excludes the cache) | `input - cached_input` |
| cache read | `cache_read_input_tokens` | `cached_input` |
| cache write | `cache_creation_input_tokens` | none |
| output | `output_tokens` | `output` |

Claude Code's status line payload carries the context window, cost, model, effort and
rate limits, but no session totals. The plugin collector (`statusline` setting) writes
them to `usage/claude/SESSION.json` under the state root:
`{schema, startedAt, since, input, output, cacheRead, cacheWrite, updated}`.

## Claude Code's statusLine slot

`codex_ui_mods.claude_statusline` owns the slot and never loses what was there:

- `detect()`: `existing` is `none`, `custom` or `ours`; `auto` is the mode auto picks.
- `enable(mode)`: `replace` installs the full layout; `extend` keeps the existing
  command's lines first and unchanged (same stdin, its own shell, bounded) and adds a
  line of extras, by default `effort,tier,limits,session,cache`, so folder, model and
  context are not shown twice; `auto` extends a custom command, else replaces. The
  existing object is backed up exactly either way. Repeating it changes nothing; a mode
  switch rewrites only the backup's choice, never nests the command.
- `disable()`: restores the backed-up object exactly, keeps unrelated settings edits,
  and refuses if the slot was edited after enable.
- `render()`: the renderer the slot runs; the state root is resolved in process.
