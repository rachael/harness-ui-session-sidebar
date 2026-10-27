# Native status line

Optional, independent module for stock Codex `rust-v0.164.0`. Enable with
`CODEX_UI_STATUS_LINE=1`; absent/other values preserve the existing native status
line selections. Modes never rewrite the native configuration file.

`HARNESS_UI_STATUS_LINE_MODE=auto` is the default: a missing selection or the
upstream default list gets the full layout; an explicitly different/reordered
native selection gets **Extra content**. Set `extend` to retain the native row,
order, colors and links while enriching selected context/session labels and adding
only missing metrics where space permits. Set `replace` for the full layout.
Turning the module off restores the native rendering of the unchanged selection. No service, log polling, shell formatter, model call or extra
pricing table is introduced.

The visual grouping follows the shared status-line contract:

`place ‖ model · effort · tier ‖ ctx current/capacity percent ‖ quota % left reset ‖ sess uncached+out · cache read %`

Context uses native **last** token usage and effective capacity. Session counts
cumulative uncached input plus output. Cache read is cumulative cached input /
cumulative input; cached input is already a subset of Codex input and is never
added twice. These are token measures, not monetary cost or estimated savings.
Compaction can reduce context while session totals continue to accumulate.
Missing measurements are omitted. A real zero is retained when reported.

Quota windows and reset labels come from the native `codex` account snapshot.
Model, reasoning effort, service tier and project/branch come from native state;
branch lookup reuses the existing asynchronous cache. No dirty marker is guessed.
The optional `cost` item appears only if Codex's existing native ledger already
provides a cost value; this module does not start another ledger poll or calculate
prices.

The row remains one line. Less important whole segments drop first: effort/tier,
cost, cache, session, secondary quota, primary quota, model. Place then truncates
and context abbreviates. At extremely small widths, context takes precedence.
Colors follow the native status-line color setting unless explicitly overridden.

Optional environment configuration:

- `HARNESS_UI_STATUS_LINE_MODE`: `auto`, `extend`, or `replace`.
- `HARNESS_UI_STATUS_LINE_ITEMS`: comma-separated order/selection; default
  `project,model,effort,tier,context,limits,session,cache` in full layout.
  Extra-content mode defaults to missing `context,session,cache` metrics and
  preserves the native base before fitting extras. `cost` is opt-in.
- `HARNESS_UI_STATUS_LINE_ASCII=1`: use ASCII separators and truncation marks.
- `HARNESS_UI_STATUS_LINE_COLORS=0` or `1`: override native color preference.

The design review considered [codex-hud v0.9.2](https://github.com/brandonwie/codex-hud/tree/4e7efc8b8f22b5711eed2b675bb73d5ead9f71f4),
which offers configurable segments under MIT. This module uses a new in-process
formatter rather than copying its collector/patch: native usage stays authoritative,
cached input is not double-counted, and refresh never runs a blocking shell command.

Tests cover cache arithmetic, compaction versus cumulative usage, native tier,
quotas, missing readings, configurable ordering and narrow widths. Run
`cargo test -p codex-tui status_hud --lib` in the patched `codex-rs` tree.
