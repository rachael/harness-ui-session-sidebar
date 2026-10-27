# Module starter

A working **data-only** module, unselected by default. It uses the existing scanner,
shared state contract and typed option schema. No empty/fake native patch is shipped.
The smoke starts a candidate with `--version`, checks the boolean option's runtime
value and exits without a model call. It does not claim to verify a new UI feature.

Try the copy/scan/enable/data flow in an isolated home:

```sh
example_home=$(mktemp -d)
mkdir -p "$example_home/modules"
cp -R templates/module "$example_home/modules/my-module"
export HARNESS_UI_HOME="$example_home"
export HARNESS_UI_STATE_DIR="$example_home/state"
harness-ui modules scan
harness-ui modules enable my-module
harness-ui state publish-session codex example --input-file "$example_home/modules/my-module/example-session.json"
harness-ui surface note --session example --text 'The example record is ready.' --source-anchor 'example:1'
harness-ui state paths --provider codex
python3 "$example_home/modules/my-module/smoke.py" /path/to/installed/codex
```

The state is real JSON in the temporary state root. The example record is fictional;
a native viewer needs the matching session ID to display it. No hooks, global
configuration, compiler or inference are needed for the data flow. Use provider
`claude` for the same contract. Keep runtime data outside the module directory.

Change the ID/description/version for your own module. `example_flag` demonstrates
an existing boolean option: the installer stores it in
`module_options.my-module.example_flag` and supplies `HARNESS_UI_EXAMPLE_FLAG=1|0`
only when selected. Read the flag in your own implementation; it does not invent
an extra renderer. Choice options use unique string `choices` and a matching default.

## A native UI change

Start from the nearest actual module's style and integration points:

- `modules/info-pane`: shared session/section/response view; `info_pane`,
  `owned_transcript`, `response_pane` tests.
- `modules/context-drawer`: pinned prompt interaction; `context_drawer` tests.
- `modules/statusline`: native single-row layout; `status_hud` tests.

Keep the source patch in the module, list it under `patches`, pin exact `codex`
versions, add a meaningful filtered test and a feature-specific isolated native
smoke. A patch module must report more than zero passing tests before activation.
The existing modules' fullscreen smokes provide concrete examples; copy only what
your feature actually needs. Set an opt-in runtime flag for independently disabled
behavior. Do not introduce a new scheduler, data store or build framework.

## Template audit after the first three UI modules

The three concrete cases share a versioned manifest, independent runtime enable
flag, ordered patch application, positive filtered tests and native verification.
Those are retained. Their rendering/event logic and interaction-specific smoke
assertions differ, so those stay in their modules. The VT reader is currently
shared in only two smoke implementations; this audit does not create another
abstraction from it. The data-only starter uses the already-supported contract;
adding a framework or speculative plugin API is not needed for these cases.
