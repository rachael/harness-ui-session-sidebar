# Optional shared session sidebar

The optional shared session sidebar and its associated session sharing are opt-in and off by default. It shows machine-local participating session status; it does not send sessions over a network. Other selected UI features can publish their own local state independently.

Enable with `harness-ui modules enable session-sidebar`, then install session hooks with `harness-ui codex install-hooks`. The quick installer handles both when this module is selected.

Open with `harness-ui sessions layout -- harness-ui launch` (Codex) or `harness-ui sessions layout -- claude`. Requires tmux. Inside tmux this explicitly splits the current pane; outside tmux it creates and attaches a session. Ordinary launch leaves your layout unchanged.

Rows group sessions by harness and show native activity reports. Switching is restricted to supported same-window and pane boundaries; unsupported hosts return an explanation. Use `harness-ui sessions list` to inspect announced sessions. Installer options control tab switching, pane switching, and which harnesses are visible.

This module adds hooks and Python UI only; it does not change the native binary or trigger a Rust build by itself.
