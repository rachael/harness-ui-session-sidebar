# Info pane

Optional native pane for Codex 0.164.0. Enable with `harness-ui modules enable
info-pane` and prepare the selected modules with `harness-ui update --yes`.
The launcher supplies `CODEX_UI_INFO_PANE=1`; without it Codex retains stock layout.

The pane appears in fullscreen terminals at least 144 columns wide. Narrow or
inline terminals retain the normal transcript. It reads the shared
[overview contract](../../contracts/overview.md): title, recap, tasks, asks, pins,
verified surfaced items and arbitrary provider sections. Empty sections stay hidden.

The Responses tab uses native assistant replies. Click a row to jump to its source;
use the separate `+` / `−` control for a short summary. Hover does not expand a list.
F6 focuses response navigation; arrows choose a row, Space expands, Enter jumps.

An enabled native pet moves into reserved footer space when its complete sprite
and status clearance fit, otherwise it retains stock placement. The footer leaves
at least twelve rows for content. `HARNESS_UI_PET_FOOTER_PADDING` accepts 0–6 rows
(default 1). With native pets disabled, a session-stable static mascot uses the
shared sprite catalog. Local click actions show a greeting or recap, or choose a
different mascot; they never enable pets, animate or call a model. Hover or F7
reveals the spaced interaction bubble; Enter activates and Esc releases focus.

State defaults to `~/.local/state/harness-ui-mods`; `HARNESS_UI_STATE_DIR` overrides
it (`CODEX_UI_STATE_DIR` remains an alias). Publish through `harness-ui state` or
`harness-ui surface`. A provenance envelope is read through its `data` field and
can only be updated through its configured publisher. The native UI calls no
model to read state or derive response excerpts.

The independent patch composes with `context-drawer`. Filtered Rust tests cover
layout, response navigation, pet fallback and verified-item rendering. `smoke.py`
checks a real isolated native PTY at wide/narrow widths and with the pane disabled;
it uses a local fixture and submits no model turn.

Codex-derived changes retain the upstream Apache-2.0 license and NOTICE. Shared
original sprite data and patcher code use the repository's MIT license.
