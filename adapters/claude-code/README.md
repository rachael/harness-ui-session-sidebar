# Claude Code adapter

One Claude Code plugin (`info-pane`) with three features, each switched on its own:

| Feature | Setting | Default | Pairs with |
| --- | --- | --- | --- |
| Info pane | `infoPane` | on | Codex `info-pane` |
| Prompt context drawer | `contextDrawer` | on | Codex `context-drawer` |
| Status line collector | `statusline` | off | Codex statusline module |

The pane inherits the harness background and follows live theme changes.
Settings are the plugin's `userConfig`: the `/config` menu, or
`claude plugin configure info-pane@harness-ui-mods`, or `harness-ui claude features`.
Installs go through Claude Code's own plugin location (`claude plugin install`, below),
never a copy into `~/.claude/skills`; existing settings are kept.

## Info pane

Docked beside the transcript in fullscreen (`/tui`), opened at session start or with
`/info-pane`. Two tabs (`o` and `r` while the pane has the keyboard):

- **Overview**: title, harness, folder and branch, recap; **Asks** and **Notes** an
  agent surfaced; extra sections; **Pinned**; **Last answer**; **Waiting on**; tasks.
- **Responses**: one row per reply, its first line. `[+]` shows a short summary under it
  and `[-]` hides it; pressing the row jumps the transcript to that reply. Rows never
  expand on hover.

Each reply in the transcript gets a pin (`◇`/`◆`). Questions in replies, explicit
`AskUserQuestion` dialogs and running background agents wait under **Waiting on**; the
model closes answered ones with the `mcp__info-pane__asks` tool. The title, recap and
ask summaries come from one small-model call after each turn.

The little Claude by the title answers a click with a toast, the recap, a wiggle or
another Claude. Sprites come from `assets/sprites.json`, shared with the Codex pane.

### Shared state

The pane draws the session record in [`contracts/overview.md`](../../contracts/overview.md).

- State root and badges: what `harness-ui state paths --provider claude` answers
  (explicit environment, then an enabled data module, then the default), asked once per
  load. Without the CLI: `HARNESS_UI_STATE_DIR`, else `CODEX_UI_STATE_DIR`, else
  `$XDG_STATE_HOME/harness-ui-mods`, else `~/.local/state/harness-ui-mods`.
- Reads `sessions/claude/SESSION.json` and `sections/SESSION.json` (raw or provenance
  envelope) at start and every 2 seconds; only a change redraws.
- Publishes by staging `staging/claude-SESSION.json` and running
  `harness-ui state publish-session claude SESSION --input-file … --retain-field surfaced --consume-input`
  (`HARNESS_UI_COMMAND` names another executable), only when content changed. A private
  publisher is configured for the CLI (`HARNESS_UI_PUBLISH_COMMAND`), not here.
- Without the CLI nothing is published: the pane works from memory, a restart starts it
  empty, and one staging file per session is left for inspection. The plugin never
  writes the canonical record itself, so it cannot race the CLI's lock or drop
  `surfaced`.
- Surfaced items show only when `verified` and not `resolved`, with their badge
  (`entry.badge`, then `HARNESS_UI_BADGES`, then `✳` for Claude and `C` for Codex). An
  automatic ask that matches a surfaced one shows once.

## Prompt context drawer

A row above the prompt names the prompt behind the replies in view: the nearest prompt
at or above the first transcript row on screen, or the latest prompt where the surface
does not report what is on screen (the terminal's main screen). It is drawn whenever
there is a prompt, so nothing shifts when it appears.

- **Hover** the row: the whole prompt opens upward from it as a card painted over the
  bottom of the transcript, anchored to the last row on screen. It uses the engine's
  hover styles (a shared hover scope revealing an absolutely positioned box), so the
  transcript, the composer and the scroll position do not move. Moving off the row
  closes it.
- **Click** the row or the card: the transcript jumps to the prompt.
- **Keyboard**, or a prompt longer than the card: `/context-drawer` or the row's `read`
  button opens the prompt in a focused pane. Arrows and page keys scroll inside it, `j`
  or Enter jumps, Esc closes.

### What the Claude Code plugin API cannot do (2.1.310)

These are limits of the engine, not of this plugin, and the plugin does not pretend
otherwise:

- A hover runs no plugin code: the card is revealed and hidden by the surface alone. It
  therefore cannot scroll; text past the viewport is cut with a count of the lines left
  and a pointer to `/context-drawer`.
- A positioned box is clipped to the region it is drawn in. The card is drawn in the
  transcript (not the band above the prompt, which is one row tall), so it can cover the
  transcript's viewport and no more.
- The top row of the fullscreen transcript is the engine's own sticky prompt header (the
  counterpart of Codex's context strip). No render hook reaches it, so the drawer lives
  in the band above the prompt and opens upward instead of hanging from that header.
- Where the surface does not report which transcript rows are on screen (the main
  screen, some remote surfaces), the card has no anchor and only the row, click and
  `/context-drawer` work.
- The keyboard pane docks beside the transcript in fullscreen. With the info pane
  already docked it opens as a tab there and the transcript keeps its width; otherwise
  docking it narrows the transcript while it is open, and on the main screen it opens
  above the prompt. Esc restores the layout.
- A jump to a transcript row is allowed only in answer to the person's own click or
  key, as the engine requires.
- The plugin test kit has no transcript to scroll; `findAll()` omits hover styles, so
  the tests check that a press asks for the jump and where the card is placed, not the
  paint. `drawn()` retains the declarative hover styles for structural checks.

Speaking companions show their generic line and an attached stem only on hover or
keyboard focus. Clicking wiggles; recap, next and animate companions retain their
actions. The speech row and blank gap stay reserved, and a stem overlays only an
empty sprite cell, so hover never moves pane content. Desktop sprite rows retain
monospace nonbreaking spaces. The native Codex pet and static companion remain
separate renderers; the shared sprite catalog keeps their idle artwork in sync.

Checked by hand on 2.1.310 in fullscreen (a resumed fictional session, no model calls):
the pane docks, its rows stay one line, `[+]` expands without moving the transcript, a
response row and the drawer row each jump the transcript, hovering the drawer row shows
the card over the transcript with nothing else moving, and the little Claude's speech
row keeps its place.

## Status line

Drawn by Claude Code's own `statusLine` slot through the harness-ui renderer
(`codex_ui_mods.statusline`, laid out by [`contracts/statusline.md`](../../contracts/statusline.md)),
not by this plugin: a plugin status row is drawn with an engine prefix (`⚠ info-pane:`)
that cannot be removed. Installing it is `codex_ui_mods.claude_statusline`'s job:
`auto` extends an existing custom command (their lines first, then a line of extras) or
installs the full layout in an empty slot, backs up what was there, and restores it
exactly on disable.

This plugin's `statusline` setting (off by default) is the collector: the slot's
payload has no session totals, so it sums each model request's usage (`turn.step`) and
writes them to `usage/claude/SESSION.json` under the state root. It draws nothing, and
the test kit cannot drive `turn.step`, so the sums are tested as a pure function.

## Install

From the repository's marketplace (`.claude-plugin/marketplace.json` at its root):

```sh
claude plugin marketplace add rachael/harness-ui-mods
claude plugin install info-pane@harness-ui-mods
```

The plugin is self-contained: its state commands run from its own `runtime/` (a
generated copy of the shared Python sources) under `python3`, 3.11 or later, the one
requirement. They read and write local files only; nothing is downloaded or built.
`HARNESS_UI_COMMAND` names an installed `harness-ui` to use instead.

### While developing

An installed plugin picks up changes only on `/reload-plugins` or a new session. To have
edits reload on save, load the source folder instead of an install, and leave the
installed copy disabled meanwhile so two copies don't both run:

```sh
claude --plugin-dir /path/to/harness-ui-mods/adapters/claude-code
# or, for every session: CLAUDE_CODE_PLUGIN_DIRS=/path/to/harness-ui-mods/adapters/claude-code
```

A marketplace added from a local checkout (`claude plugin marketplace add /path/to/harness-ui-mods`)
is also read in place: edit, then `/reload-plugins`.

## Checks

```sh
claude plugin validate adapters/claude-code
claude plugin test adapters/claude-code
python3 -m unittest tests/test_claude_adapter.py
```

No test calls a model: `$.model` is answered by the tests. `tests/fixtures.ts` and
`hooks/claudes.ts` are generated from `contracts/fixtures` and `assets/sprites.json` by
`python3 tests/test_claude_adapter.py --write`; the unit test fails when they drift.
