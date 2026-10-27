# Context drawer

Optional, dependency-free source patch for stock OpenAI Codex `rust-v0.164.0`.
This module is independent of side panes and other extensions.

Codex's top context strip shows the user prompt associated with the first visible
answer. With this module enabled, hover over that strip to inspect the complete
prompt in place. Moving outside the drawer collapses it. Click the strip or drawer
to jump to the original prompt in the transcript, retaining the stock navigation.

The drawer paints over transcript rows; it does not reserve additional layout
space or move the composer, transcript viewport, or existing selection. Long
prompts have a separate scroll position: wheel inside the drawer to read all rows
without scrolling the transcript. Content uses the original cell's existing
sanitized transcript rendering, including wrapping and attachment labels.

Without mouse-motion support, press **F5** while the context strip is visible.
Use Up/Down, PageUp/PageDown, Home/End to read, Enter to jump, and Esc/F5 to close.
Typing closes the drawer and continues through the ordinary composer input path.
Configured application shortcuts retain their existing precedence. The drawer
closes when its context strip disappears, on focus loss, and for modal overlays.
The stock strip requires at least 16 columns and four transcript rows; below those
sizes neither a drawer nor a new keyboard capture is introduced.

`CODEX_UI_CONTEXT_DRAWER=1` enables the module when the TranscriptView is constructed.
Absent or any other value leaves stock interaction and rendering intact. A modular
launcher should set this variable only when the module is enabled. The patch does
not change terminal mouse capture or add a service, dependency, network access,
persistent state, or provider-specific file path.

Tests in `context_drawer_tests.rs` cover retained viewport/selection, full overflow
reading, click navigation, keyboard fallback, narrow resizing, composer bounds,
and stock behavior when disabled. Run `cargo test -p codex-tui context_drawer` in
the patched `codex-rs` directory. The standalone smoke script resumes synthetic native history in an isolated
fullscreen PTY using the exact selected binary. It sends hover, wheel, click, F5,
Escape and resize events, verifies restored viewport/composer state, and retains
raw ANSI plus reconstructed terminal screens. Its sole configured provider is
unauthenticated localhost:9; it never submits a model request. Run:

```sh
python3 smoke.py /path/to/codex --output /tmp/drawer-smoke
```

For authorized views, `--prompt-file /outside/repo/prompt.txt` supplies capture text
without embedding it in public source. The Rust renderer capture fixture similarly
accepts `CODEX_DRAWER_CAPTURE_PROMPT_FILE` and writes cell/style JSON when
`CODEX_DRAWER_CAPTURE_DIR` is set. Both use fictional text by default. Captured
views distinguish a real binary PTY from a renderer test fixture; neither is
represented as a desktop screenshot. A failing smoke exits 1 and records its
actual last screen, rather than claiming gestures were validated.

Modified existing source files contain modification notices. The source patch is
against the Apache-2.0-licensed upstream; retain upstream copyright and license
notices when distributing the patched source or binary.
