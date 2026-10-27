# Detailed guide

The compact [README](../README.md) contains the default install and individual choices.

# Harness UI Mods

Select UI modules for Codex CLI and Claude Code, or write your own. The launcher uses a
patched binary only when its stock version, stock executable hash, selected
modules and binary hash match. After a Codex upgrade it immediately delegates to
stock until a compatible replacement is verified.

This is a local development candidate. No published binaries or tested platform
matrix are claimed yet. A Rust source build can take 20–30 minutes or longer.
The patcher itself uses Python 3.11+ and its standard library; builds additionally
need Git, Cargo and the toolchain required by the selected Codex release.

## Install and choose modules

After the repository is published, either command installs the CLI directly from
GitHub without a package registry account:

```sh
uv tool install 'git+https://github.com/rachael/harness-ui-mods.git'
# or: pipx install 'git+https://github.com/rachael/harness-ui-mods.git'
harness-ui install          # autodetect installed harnesses; future updates ask
# Optional policies: harness-ui install --auto   or   harness-ui install --off
harness-ui launch
```

Install selects current built-ins for detected Codex/Claude Code, prepares a
matching payload and accepts defaults without additional questions. Existing
explicit module disables and pet configuration remain unchanged. For an advanced
manual flow, use `setup --stock PATH`, `modules enable ID`, then `update --yes`.

The stock command is never replaced. `harness-ui launch -- --help` forwards arguments.
`--home /some/directory` selects isolated configuration, catalog, builds and state;
otherwise `HARNESS_UI_HOME` (or the explicit legacy `CODEX_UI_HOME`) or `~/.local/share/harness-ui-mods` is used.

`modules disable ID` changes the selection immediately, causing stock delegation
until that exact selection is prepared. Dependencies are included automatically;
missing dependencies, cycles, conflicting patches and incompatible versions fail
before activation. Existing installed builds remain on disk for inspection, but
incompatible ones are never launched.

The Codex info-pane install also merges the generic overview hooks into the
existing hooks file, preserving unrelated hooks. Hooks reduce native lifecycle,
explicit questions, answered responses and plan updates into the shared session
record; they do not call a model. Hook trust is handled by Codex's native `/hooks`
review flow. Existing manual installs can inspect or apply the same merge:

```sh
harness-ui codex install-hooks --check
harness-ui codex install-hooks
```

## Component packages and aliases

The same package supplies `harness-ui`, `codex-ui`, and `claude-ui`, using the same manager home and state. The latter two default `install` to their named harness; an explicit `--harness` wins. `codex-ui launch` keeps the existing Codex launch behavior, while `claude-ui launch` starts the configured native Claude command with your arguments.

`install --module info-pane` adds a component, preserving the other components, options and off choices. Add another with `install --module context-drawer`. Repeating an included component reports `already_included` without re-enabling an off component. Inclusion is recorded separately for each harness in the existing manager config. The full source package keeps the same single plugin and settings; availability does not enable the optional sidebar or other off choices. To change those choices deliberately, use the installer checkboxes or `--replace-selection --module NAME`. Unchecking a component removes it even when arriving through an additive component preset. `--all-modules` remains an explicit opt-in and respects recorded disables.

The seven generated repositories all contain the full cross-harness source: `harness-ui-mods`, `codex-info-pane`, `claude-code-info-pane`, `codex-tui-hud-extender-statusline`, `claude-code-tui-hud-extender-statusline`, `harness-ui-context-drawer`, and `harness-ui-session-sidebar`. Their individual source downloads preselect the relevant component; they do not install another plugin or create a fork. Full-source downloads use ordinary defaults and preserve existing selections.

## Claude Code plugin

`harness-ui install --harness claude` installs through Claude Code's native marketplace cache using Claude's native `plugin install --marketplace` command against the reviewed local catalog. It validates the adapter first and verifies the native installation afterward. Run `/reload-plugins` in an existing Claude session.

An existing native installation keeps its enabled state and feature choices when you accept unchanged defaults. Explicit module selections use Claude's native plugin configuration API. Existing legacy skills-directory installations are preserved and require an explicit migration; the installer never creates or overwrites a skills-directory plugin. If both native and legacy copies are enabled, it refuses before changing either owner.

For development, use `claude --plugin-dir /path/to/adapters/claude-code` or `CLAUDE_CODE_PLUGIN_DIRS`; these are temporary development paths, not installation destinations. `harness-ui claude install --dry-run` inspects ownership without copying or configuring anything.

The native statusline supports `harness-ui claude statusline enable --mode auto`, `check`, and `disable`. Auto uses the full layout for a basic setup and extends an existing custom command. Explicit replacement is backed up and reversible. Existing disabled features stay disabled on an ordinary rerun. No pet or animation setting changes by default.

## Optional shared session sidebar

The optional shared session sidebar and its associated session sharing are opt-in and off by default. It shows machine-local participating session status; it does not send sessions over a network. Other selected UI features can publish their own local state independently.

Select `session-sidebar` (`install --module session-sidebar`) to install the session hooks. The native Claude session list stays in its right pane. Tab and pane switching, plus visibility for each harness, are independently configurable. Open the left sidebar with `harness-ui sessions layout -- harness-ui launch` for Codex or `harness-ui sessions layout -- claude` for Claude Code. This requires tmux; ordinary harness launch does not automatically rearrange your terminal. `harness-ui sessions list` lists announced sessions. Switching is limited to the supported terminal/window and pane boundaries; unsupported hosts report that limitation. Saved sidebar options apply to the layout command.

To opt out for future sessions, deselect the sidebar in the installer or use `harness-ui modules disable session-sidebar` for Codex and `harness-ui claude features disable session-sidebar` for Claude Code. Existing saved records are not deleted. Claude's native plugin `activity` setting (`auto`, `on`, `off`) is separate: `auto` publishes activity while a plugin feature is enabled; `off` stops activity publishing. Other selected features still manage their own local records. There is no implied global state-sharing switch.

`install --no-build` and `update --no-build` require a compatible verified bundle and refuse a Cargo fallback. Source builds otherwise can take 20–30 minutes or longer on a cold machine.

## Discover or author modules

```sh
harness-ui modules init my-module
harness-ui modules add-path /absolute/path/to/my-trusted-modules
harness-ui modules scan
harness-ui modules enable my-module
```

A scan reads `*/module.json` under the managed catalog's `modules/`, the default
user modules directory and explicitly added directories. It never imports or runs
a module. Duplicate IDs are errors. Newly discovered modules stay disabled by
default. `config enable-new true` deliberately enables future discoveries from
these trusted directories, including their code; use that only when appropriate.
`modules all` enables today's modules and does not enable future discoveries.
Explicit `modules disable ID` overrides `enable-new` until explicitly re-enabled.

See [the template](../templates/module/module.json). The version-1 JSON schema is:

| Field | Meaning |
| --- | --- |
| `schema` | Integer `1` |
| `id`, `version`, `description` | Stable lowercase ID, module release, description |
| `harness` | `codex` (default) or `claude`; Codex builds never select Claude modules |
| `codex` | Exact compatible version strings; `"*"` for version-independent data |
| `order` | Integer; ascending order among independent modules |
| `dependencies` | Module IDs, applied first |
| `patches` | Relative patch files; paths must stay inside the module |
| `tests` | Objects with `package` and Cargo test `filter`; required for patches |
| `smoke` | Optional argument arrays, run in the module directory; no shell |
| `runtime_env` | `HARNESS_UI_*` or `CODEX_UI_*` string variables, applied only to an active build |

Example test: `{"package":"codex-tui","filter":"my_module"}`. Each filtered suite
must report at least one passing test. Smoke arguments may use `{binary}` (candidate
executable) and `{source}` (upstream source worktree; empty for downloaded artifacts).
Use executable scripts or an explicit interpreter such as `python3`. Smoke commands
are trusted code and should use isolated temporary homes and stop before inference.

A local data module can have empty `patches`, `tests` and `smoke` arrays and set
`HARNESS_UI_STATE_DIR`. It uses the same scanner as UI modules; no special integration
is required. A data-only selection keeps the original stock executable in its package
layout and supplies runtime variables; it never copies a relative-path launcher.
Its smoke checks use that original executable and configured runtime environment.

Enabling a module trusts its patches, build tests, smoke commands and runtime data
configuration. There is no sandbox around enabled module code. Keep scan roots
limited to directories whose authors you trust.

## Update modes

```sh
harness-ui config updates auto   # check in background on launch, at most every 6 hours unless the stock fingerprint changes
harness-ui config updates ask    # stock after drift; ask before executable source fetch
harness-ui config updates off    # only explicit update commands prepare changes
harness-ui check                 # local status, no changes or network
harness-ui check --online        # bounded JSON compatibility metadata only
harness-ui update               # prompts only with an interactive stdin
harness-ui update --yes          # explicit approval for unattended operation
```

In `auto`, launch starts a detached updater and immediately runs the compatible
installed build or stock. It never waits for Cargo. Updater output goes to
`updates.log`. In `ask`, launch may download JSON metadata and notify that a
compatible patch is ready. It does not fetch Git source, executable module content
or a binary until approval. Noninteractive update returns pending (exit 2), never
waits on a prompt. Terminal notification is the supported notification channel.
There is no background daemon: launches and explicit checks drive checks; an
operator may schedule `harness-ui update --yes` using their existing scheduler.

A trusted catalog Git remote and branch are configured at setup. Update fetches
that source (including the patcher's own implementation) and fast-forwards only.
It refuses dirty, diverged, wrong-branch or wrong-remote checkouts. After an approved refresh, later installed commands and Codex hooks also use the verified managed controller. Dispatch checks the recorded approval against the clean checkout, commit and configured origin before importing its code. It never fetches or prompts. Missing, edited or mismatched managed source falls back to the installed controller. A pinned `--no-refresh` install can continue using its installed code until a refresh is approved. Upgrade the installed bootstrap itself with `uv tool upgrade harness-ui-mods` or `pipx upgrade harness-ui-mods`.

Configuration is plain `config.json` under the selected home. Relevant keys include
`disabled`, `stock`, `updates`, `scan_paths`, `enabled`, `enable_new`, `catalog_remote`,
`catalog_branch`, `metadata_url` (JSON only), and `upstream`. Changing trusted remotes
is an explicit trust decision. Never add credentials to these URLs.

## Surface important content once

An assistant can explicitly consolidate a question, note or decision into the pane
instead of repeating it in chat. This is a control command, not a classifier:

```sh
harness-ui surface ask --session SESSION --text 'Which option should we use?' --source-anchor 'message:42'
harness-ui surface note --session SESSION --text 'The fixture checks passed.' --source-anchor 'message:43'
harness-ui surface decision --session SESSION --id chosen-layout --text 'Keep the compact layout.' --source-anchor 'message:44'
harness-ui surface resolve --session SESSION --id chosen-layout
```

State lives at `$HARNESS_UI_STATE_DIR/sessions/codex/SESSION.json` (or the standard
user state directory). `--provider` changes the provider segment. The command
preserves other fields and appends to top-level `surfaced`:

```json
{"surfaced":[{"id":"stable-content-id","kind":"note","text":"The fixture checks passed.",
"source_anchor":"message:43","surfaced_by":"codex","verified":true,"status":"open"}]}
```

Without `--id`, a stable ID is derived from kind, exact text and source anchor.
Repeating it returns `already_present`; a new entry returns `published`. An ID
already used for different content is rejected. Resolved entries remain in state
with `status: "resolved"` and should be hidden by renderers. A new interaction
needs a new ID; repeated publishing does not reopen resolved items.

The writer locks the session, reads before writing, publishes an unverified entry,
reads it back, confirms `verified: true`, then reads back again. It reports success
only after both checks. A renderer may show a small Codex badge only for verified
entries with `surfaced_by` matching its provider. A successful result lets the caller avoid a
duplicate chat message; failure requires a visible fallback in the calling UI.

Raw JSON files are written atomically. An existing schema-1 provenance envelope
(`schema_version`, `revision`, `data`) is never overwritten directly. To use an
external state owner, explicitly configure `HARNESS_UI_PUBLISH_COMMAND` as a JSON
argument array, for example `["my-publisher","{provider}","{session}"]`. It receives
the merged full raw record on standard input, without a shell. The command must
publish to the same path; a zero exit without persistence is not accepted.
`HARNESS_UI_EXPECT_REVISION` contains the last-read envelope revision (empty when
none exists), refreshed between both writes. The publisher must enforce that
revision under its own canonical lock and fail on conflict. The caller gets a
retryable error rather than a blind overwrite. Generic raw-state writers should
share the adjacent `SESSION.surface.lock` to avoid lost updates.

The neutral state/publisher/revision variables take precedence; `CODEX_UI_*`
aliases remain supported. `surface --provider claude` writes Claude-origin entries.
Badge defaults are `C` for Codex and `✳` for Claude. `HARNESS_UI_BADGE` overrides one
invocation; `HARNESS_UI_BADGES` accepts a JSON provider-to-badge registry. Entries
record their badge alongside the verified provider identity.

An adapter launched normally can resolve the same shared state without invoking
Codex or Cargo:

```sh
harness-ui state paths --provider claude
```

The JSON result includes `state_root`, `publisher_command` (argv with `{session}`
left for the caller) and the effective state/publisher environment. Resolution
reads enabled trusted data-only modules through the same scanner. Explicit process
environment takes precedence over module configuration, then defaults; different
module values for the same state/publisher setting are rejected. Neutral and legacy
variable names are treated as aliases. Surface and publish-session use this same
resolution, including when only Claude Code is installed.

Adapters can publish their authoritative full session record through the same
writer and lock, retaining externally surfaced content:

```sh
harness-ui state publish-session claude SESSION --input-file /temporary/session.json --retain-field surfaced --consume-input
```

The input must be an object. A retained field survives when absent from the input;
an explicitly supplied field replaces it. Consume removes unchanged input only
after successful read-back. Envelope state still delegates to the configured
publisher with revision checks. No shell is used, and recursive publishers fail.

## Builds and downloads

Updates take a nonblocking lock. Source builds use a fresh managed detached Git
worktree for the exact `rust-vVERSION` upstream tag on every attempt. Patches are
applied in dependency/order sequence, then filtered tests, a release build and smoke
checks run before atomic installation. Snapshot fixture version placeholders are
stamped for the release. Failed or dirty attempt directories are retained; nothing
runs `git reset --hard` or `git clean` on them. Build cache lives under `target/`.
Review and remove obsolete attempts yourself when no build is active.

The catalog may provide license-bearing tar.gz bundles in `catalog.json`:

```json
{"schema":1,"modules":[{"id":"example","codex":["0.164.0"]}],"bundles":[{
  "codex":"0.164.0","platform":"darwin-arm64",
  "modules":{"example":"MODULE_PATCH_SHA256"},
  "url":"https://example.invalid/releases/codex.tar.gz",
  "sha256":"BINARY_SHA256"
}]}
```

Only an exact stock version/platform/patch-module match is downloaded. HTTPS,
archive SHA256, executable SHA256, candidate version and module smoke checks are required before activation.
Each archive must contain exactly four regular files: `codex`, `upstream/LICENSE`,
`upstream/NOTICE`, and `source.json`. The latter records `schema: 1`, `codex`, an
exact `upstream_commit`, `binary_sha256`, and the same patch-only `modules` hash mapping.
Absolute/traversing paths, links, special files, duplicates, missing notices and
unexpected files are rejected. The installation stages the entire directory and
atomically publishes its pointer only after verification, preserving license,
NOTICE, modification attribution and source metadata. Raw binary downloads are
not accepted. Source builds likewise require upstream LICENSE and NOTICE.

The catalog is the trust root; a checksum detects corruption, not a malicious
catalog maintainer. Other combinations fall back to source builds. No bundle is
advertised as available until its publisher has built and verified it. Bundles
represent exact patch-module selections; arbitrary subsets require source builds
unless separately published and verified. Data-only modules never invalidate a
binary bundle. `patch_hash` excludes runtime environment configuration, while
`content_hash` tracks the complete module tree. Installation records separate
binary-patch and full runtime fingerprints; runtime-only changes reverify smoke
checks and reuse the existing binary without Cargo.
Codex 0.164+ installations also need the same-version `codex-code-mode-host` next
to the executable. The patcher locates it beside stock or in stock's platform npm
package, fingerprints both native executable and helper, and preserves the helper
for source and downloaded builds. Missing modern helpers prevent activation;
changed helper hashes force stock fallback. No extra host compilation is required.

Module fingerprints cover the whole module tree (relative names and bytes),
excluding `.git`, `__pycache__`, and `.pytest_cache`; symlinks are rejected. Keep
runtime state and smoke output outside the module directory so changing data does
not invalidate code fingerprints.

Installation metadata records version, module hashes, executable hash, profile,
upstream commit (source builds) and preparation time. A stock or selection change
during preparation prevents activation.

## Development and release

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m codex_ui_mods.cli --help
```

Tests use local disposable Git repositories and a fake Cargo executable; they do
not compile Codex or perform inference. Before release, independently review the
exact commit and full repository history, build the supported platform/module
combinations, run each module's fullscreen smoke checks, and only then upload
binaries with their SHA256 and exact module hashes. Publish a tagged GitHub release;
no registry account is needed. Never copy a user's state, configuration or local
integration modules into the release repository.

## Five synchronized full-source repositories

[Harness UI Mods](https://github.com/rachael/harness-ui-mods) is authoritative.
[Codex Info Pane](https://github.com/rachael/codex-info-pane) and
[Claude Code Info Pane](https://github.com/rachael/claude-code-info-pane) are generated releases alongside Codex TUI HUD Extender Statusline and Claude Code
TUI HUD Extender Statusline. Every default branch contains the full cross-harness
source, package, Pages demo and individual source presets; titles help discovery.
They link back here and are never edited as independent forks.

```sh
python3 tools/export_repos.py --revision REVIEWED_COMMIT --output /new/export-directory
```

The exporter reads only explicitly allowlisted committed Git blobs, not working
changes or untracked files. It creates seven new clean directories without Git
history, records source commit/file hashes in `SOURCE.json`, rejects selected
symlinks/submodules and refuses existing output directories. Codex exports require
committed `LICENSES/Apache-2.0.txt` and `NOTICE`; adapter/module source must also be
committed first. Review and scan all seven generated trees, then initialize their
fresh public histories and publish their coordinated release tags. Exporting does
not push, publish, install anything globally or modify user configuration.

## Licensing

The Python patcher and template are MIT licensed. Patches modifying upstream Codex
are derived from Apache-2.0 licensed Codex source and retain that license and its
notices. These separate licenses do not relicense each other's code. Distributors
must include upstream Apache-2.0 license/NOTICE material with patched binaries and
preserve each module's provenance. A release must review the assembled notices
before publication.

## Reuse decision

[Codex HUD](https://github.com/brandonwie/codex-hud) was evaluated as an existing
MIT-licensed implementation (13 stars when checked). Its maintained source and
published-runtime flows are useful precedent; its single-footer design and
continued use of an older patched runtime while a release is pending do not meet
this project's module selection and strict stock-fallback contract. No source was
copied. Git/Cargo perform patching/builds; standard Python packaging supplies the
CLI entry point, following [PyPA](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
and [uv tool](https://docs.astral.sh/uv/guides/tools/) documentation. Keeping the
lifecycle small avoids adding a Node runtime, but leaves this project responsible
for its own compatibility and release testing.


## Quiet discovery and explicit selections

Install defaults apply on the first successful install. A normal rerun preserves
previous harness/module choices, explicit disables and update policy. A newly
supported harness is offered rather than silently installed. Use `--harness`
explicitly to add one, `--module ID` (repeatable) for an individual selection, or
`--all-modules` to request today's full set while preserving explicit disables.
Fresh installs include the current statusline feature. Auto mode uses the full
layout for a basic setup and extra content for clearly customized statuslines.
Previously disabled/off features stay that way on managed reruns. Explicit
--module selections are honored; --mode never implicitly enables statusline.

`harness-ui install --dry-run` prints the local plan without network requests,
native harness execution, configuration changes or installation. Adapter feature
metadata drives native Claude userConfig keys; options not named stay unchanged.

New optional features are noticed once and never enabled by the default flow:

```sh
harness-ui notices
harness-ui notices dismiss
harness-ui notices remind-later --hours 24
harness-ui notices suppress
harness-ui notices resume
```

The existing advanced `config enable-new true` remains an explicit trust and
automatic-enrollment choice for trusted scan roots. Installation never sets it;
it is distinct from the default notice-only behavior.

Each generated GitHub Pages site has a labeled synthetic interactive simulation,
actual native rendered views labeled as terminal-cell fixture renders, full source
and individual harness/module preset downloads with SHA256 checksums. Source
presets include the full shared engine with a scoped install bootstrap. The
committed shared site template is copied by the exporter, including a standard
GitHub Pages workflow. Publishing remains a separate reviewed action.


Statusline integration extends the existing layout by default when explicitly
selected, rather than replacing user scripts. The same package can select the
optional full layout. Known integrations can compose configurable extra content;
there is no claim that arbitrary scripts can be semantically deduplicated.


## One-screen keyboard install

In a terminal, install opens one screen with **Accept and continue** focused at the
top. Enter installs the preselected defaults. Move down only to change something:
Up/Down navigate, Space selects a module, Right expands its options, Space toggles
a boolean or cycles a declared choice, and Left collapses. There is no second
confirmation screen. A module-specific bootstrap or download preselects only that
module/harness; the same default branch still contains the full shared package.
Non-TTY input never opens the chooser. `--non-interactive` uses defaults/flags.

Custom module options are typed metadata, not executable configuration:

```json
{"options":[{"id":"switch_tabs","label":"Switch tabs","type":"boolean","default":true,
"runtime_env":"HARNESS_UI_SIDEBAR_SWITCH_TABS","config_key":"switchTabs"}]}
```

Supported types are boolean and choice (unique string `choices`, with a matching
`default`). Values are stored as `module_options[module_id][option_id]`. Only
selected modules receive declared UI runtime variables; native Claude options use
their declared userConfig key via the native partial-write API. No arbitrary
configuration-file keys are written. Headless example:
`--option sidebar.switch_tabs=false`.

## Native statusline controls

```sh
harness-ui claude statusline detect
harness-ui claude statusline enable --mode auto
harness-ui claude statusline enable --mode extend --items effort,tier,limits,session,cache
harness-ui claude statusline disable
harness-ui statusline render --provider claude --columns 100 --ascii --no-color
```

Auto uses the full layout for an empty/basic slot and extends a clearly custom
command. Existing command output is kept first and unchanged; configurable extras
follow. Explicit replace retains a reversible backup. Disable restores that backup
only while the slot still matches the managed command, refusing later edits.
Managed install reruns leave a disabled slot disabled. A mode choice alone does
not enable an unselected module. The renderer supports item order, ASCII and color
preferences and resolves shared raw/envelope state through the normal data module.


`harness-ui check` also reports the managed source commit when a catalog exists.
For native Claude statuslines, `claude statusline disable` is the scoped restore
command; it never overwrites a slot edited after installation.


The full-source repositories also include the native Claude marketplace manifest.
Use `claude plugin marketplace add rachael/harness-ui-mods`, then
`claude plugin install info-pane@harness-ui-mods`. The adapter's bundled runtime is
generated from the shared source; it is not a separately maintained fork.


Use `install --no-build` or `update --no-build` for a bounded first deployment:
compatible verified bundles/reuse are allowed, but missing coverage refuses Cargo
fallback. The normal default remains unchanged and no extra installer step is added.
