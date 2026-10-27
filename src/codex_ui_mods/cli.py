"""Command line interface. No shell shims or stock-command replacement."""
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time


def refreshed_manager(manager, install_harness=None):
    """Refresh trusted source, then activate only its verified approval receipt."""
    manager.refresh()
    from .bootstrap import dispatch
    arguments = [*sys.argv[1:], '--refreshed']
    if install_harness is not None: arguments += ['--harness', install_harness]
    dispatch(argv=arguments, home=manager.root)
    return manager


def main(argv=None, default_harness=None):
    from .bootstrap import dispatch
    if dispatch(argv=argv, default_harness=default_harness): return 0
    from .core import Error, Manager
    p = argparse.ArgumentParser(description='Selectable UI modules for coding harnesses')
    p.add_argument('--home', type=Path)
    sub = p.add_subparsers(dest='action', required=True)
    from . import sessions
    sessions.add_cli(sub)
    setup = sub.add_parser('setup')
    setup.add_argument('--stock', required=True)
    setup.add_argument('--updates', choices=['auto', 'ask', 'off'], default='off')
    setup.add_argument('--catalog-remote', help='Explicit trusted Git source for code and built-in modules')
    easy = sub.add_parser('install', help='Install built-ins for detected harnesses with sensible defaults')
    easy.add_argument('--harness', choices=['all', 'codex', 'claude'])
    easy.add_argument('--stock')
    easy.add_argument('--replace-selection', action='store_true', help='Replace selected harness components instead of adding to existing choices')
    easy.add_argument('--mode', choices=['auto', 'extend', 'replace'], default='auto', help='Statusline layout policy; only applies when statusline is selected')
    easy.add_argument('--no-build', action='store_true', help='Require a compatible verified binary; never fall back to Cargo')
    easy.add_argument('--non-interactive', action='store_true', help='Use selected defaults/flags without a keyboard chooser')
    easy.add_argument('--option', action='append', default=[], help='Declared MODULE.OPTION=value (repeatable)')
    easy.add_argument('--claude-settings', type=Path)
    easy.add_argument('--dry-run', action='store_true', help='Print the local plan without network, native execution or writes')
    selection = easy.add_mutually_exclusive_group()
    selection.add_argument('--module', action='append', dest='selected_modules', help='Add this component, preserving other choices (repeatable)')
    selection.add_argument('--all-modules', action='store_true', help='Opt into all current modules, preserving explicit disables')
    policy = easy.add_mutually_exclusive_group()
    policy.add_argument('--updates', choices=['auto', 'ask', 'off'])
    policy.add_argument('--auto', dest='updates', action='store_const', const='auto')
    policy.add_argument('--off', dest='updates', action='store_const', const='off')
    easy.add_argument('--claude-command', default='claude')
    easy.add_argument('--claude-destination', type=Path)
    easy.add_argument('--refreshed', action='store_true', help=argparse.SUPPRESS)
    easy.add_argument('--no-refresh', action='store_true', help=argparse.SUPPRESS)
    modules = sub.add_parser('modules')
    modules.add_argument('operation', choices=['list', 'scan', 'enable', 'disable', 'all', 'add-path', 'init'])
    modules.add_argument('names', nargs='*')
    notices = sub.add_parser('notices', help='Control one-time notices for new optional features')
    notices.add_argument('operation', nargs='?', default='status', choices=['status', 'poll', 'dismiss', 'remind-later', 'suppress', 'resume'])
    notices.add_argument('--hours', type=float, default=24)
    config = sub.add_parser('config')
    config.add_argument('key', choices=['updates', 'enable-new'])
    config.add_argument('value')
    update = sub.add_parser('update')
    update.add_argument('--no-build', action='store_true', help='Require a compatible verified binary; never fall back to Cargo')
    update.add_argument('--yes', action='store_true', help='Approve trusted source fetch, code execution and build/download')
    update.add_argument('--no-refresh', action='store_true')
    update.add_argument('--refreshed', action='store_true', help=argparse.SUPPRESS)
    check = sub.add_parser('check')
    check.add_argument('--online', action='store_true', help='Read remote JSON compatibility metadata only')
    statusline = sub.add_parser('statusline', help='Render native statusline content')
    status_commands = statusline.add_subparsers(dest='statusline_action', required=True)
    status_render = status_commands.add_parser('render')
    status_render.add_argument('--provider', choices=['codex', 'claude'], default='claude')
    status_render.add_argument('--compose-backup', type=Path)
    status_render.add_argument('--columns', type=int)
    status_render.add_argument('--items', help='Comma-separated layout segments in preferred order')
    status_render.add_argument('--ascii', action=argparse.BooleanOptionalAction, default=None)
    status_render.add_argument('--color', action=argparse.BooleanOptionalAction, default=None)
    codex = sub.add_parser('codex', help='Manage scoped Codex UI hooks')
    codex_commands = codex.add_subparsers(dest='codex_action', required=True)
    codex_hooks = codex_commands.add_parser('install-hooks')
    codex_hooks.add_argument('--check', action='store_true')
    codex_hooks.add_argument('--path', type=Path)
    codex_hooks.add_argument('--python')
    claude = sub.add_parser('claude', help='Manage the scoped Claude Code info-pane plugin')
    claude_commands = claude.add_subparsers(dest='claude_action', required=True)
    claude_install = claude_commands.add_parser('install')
    claude_install.add_argument('--ownership', choices=['auto','native','legacy','managed'], default='auto')
    claude_install.add_argument('--dry-run', action='store_true', help='Query native ownership read-only; no fetch, copy or config writes')
    claude_install.add_argument('--source', type=Path, help='Reviewed local plugin source (otherwise managed catalog)')
    claude_install.add_argument('--destination', type=Path, default=None, help='Deprecated read-only legacy destination; never copied into')
    claude_install.add_argument('--claude-command', default='claude')
    claude_install.add_argument('--adopt-matching', action='store_true', help='Deprecated compatibility flag; legacy destinations are read-only migration inputs')
    claude_install.add_argument('--refreshed', action='store_true', help=argparse.SUPPRESS)
    claude_install.add_argument('--yes', action='store_true', help='Approve trusted catalog refresh in ask mode')
    claude_features = claude_commands.add_parser('features')
    claude_features.add_argument('operation', choices=['enable', 'disable', 'all'])
    claude_features.add_argument('names', nargs='*')
    claude_features.add_argument('--plugin')
    claude_features.add_argument('--claude-command', default='claude')
    claude_features.add_argument('--mode', choices=['auto','extend','replace'], default='auto')
    claude_features.add_argument('--settings', type=Path)
    claude_status = claude_commands.add_parser('statusline')
    claude_status.add_argument('operation', choices=['detect', 'enable', 'disable', 'check'])
    claude_status.add_argument('--mode', choices=['auto', 'extend', 'replace'], default='auto')
    claude_status.add_argument('--items')
    claude_status.add_argument('--settings', type=Path)
    claude_status.add_argument('--executable')
    state = sub.add_parser('state', help='Publish session state for a harness adapter')
    state_commands = state.add_subparsers(dest='state_action', required=True)
    paths = state_commands.add_parser('paths')
    paths.add_argument('--provider', default='codex')
    publish = state_commands.add_parser('publish-session')
    publish.add_argument('provider')
    publish.add_argument('session')
    publish.add_argument('--input-file', type=Path, required=True)
    publish.add_argument('--retain-field', action='append', default=[])
    publish.add_argument('--consume-input', action='store_true')
    publish.add_argument('--merge', action='store_true', help='Merge top-level fields into the latest session record')
    surface = sub.add_parser('surface', help='Consolidate explicit asks, notes and decisions in pane state')
    surface.add_argument('kind', choices=['ask', 'note', 'decision', 'resolve'])
    surface.add_argument('--session', required=True)
    surface.add_argument('--provider', default='codex')
    surface.add_argument('--text')
    surface.add_argument('--source-anchor')
    surface.add_argument('--id')
    launch = sub.add_parser('launch')
    launch.add_argument('arguments', nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    if args.action == 'install' and args.harness is None: args.harness = default_harness
    m = Manager(args.home)
    try:
        if args.action == 'sessions':
            return sessions.handle(args, manager=m)
        elif args.action == 'install':
            from .quick import prepare
            if not args.dry_run and not args.refreshed and not args.no_refresh:
                with m.lock():
                    refreshed_manager(m, install_harness=args.harness)
            selected = args.selected_modules
            module_changes = None
            module_options = json.loads(json.dumps(m.config.get('module_options', {})))
            option_changes = {}
            display_selected = selected
            native_existing_display = False
            interactive_install = not args.dry_run and not args.non_interactive and sys.stdin.isatty() and sys.stdout.isatty()
            if (interactive_install or args.option) and not args.dry_run and args.harness != 'codex':
                import shutil
                if shutil.which(args.claude_command):
                    from .claude import native_preferences
                    manifest = m.root/'catalog/adapters/claude-code/manifest.json'
                    preferences = native_preferences(args.claude_command, manifest)
                    if preferences is not None:
                        native_existing_display = True
                        for name, values in preferences['options'].items(): module_options.setdefault(name, {}).update(values)
                        if args.harness == 'claude' and not args.all_modules and not args.replace_selection:
                            included = set(m.config.get('included_components', {}).get('claude', m.config.get('selected_modules', []) if 'claude' in m.config.get('installed_harnesses', []) else []))
                            requested = set(selected or []) - set(m.config.get('disabled', [])) - (included-set(preferences['modules']))
                            display_selected = requested | (set(preferences['modules']) - set(m.config.get('disabled', [])))
                            if not interactive_install:
                                selected = display_selected
                                module_changes = []
            if args.option:
                from .selector import catalog, parse_options
                rows = catalog(m, args.harness)
                module_options = parse_options(rows, args.option, module_options)
                option_changes = parse_options(rows, args.option)
            if interactive_install:
                from .selector import interactive
                # The native probe already supplies the complete scoped display choice.
                complete_display = args.replace_selection or (native_existing_display and args.harness == 'claude')
                choice = interactive(m, args.harness, display_selected, args.all_modules, saved_options=module_options, replace_selection=complete_display)
                if choice['module_changes'] or (native_existing_display and args.harness == 'claude'):
                    selected = choice['modules']
                    module_changes = choice['module_changes']
                for name, values in choice['option_changes'].items(): option_changes.setdefault(name, {}).update(values)
                module_options = choice['options']
            print(json.dumps(prepare(m, args.harness, args.stock, args.updates, args.claude_command,
                                   args.claude_destination, selected, args.all_modules, args.dry_run,
                                   args.mode, module_options, args.claude_settings, module_changes, args.no_build, option_changes, args.replace_selection, args.selected_modules), indent=2))
        elif args.action == 'setup':
            m.config.update(stock=args.stock, updates=args.updates)
            if args.catalog_remote:
                m.config['catalog_remote'] = args.catalog_remote
            m.stock(); m.save()
            print('Configured. Enable reviewed modules, then run harness-ui update --yes.')
        elif args.action == 'modules':
            if args.operation == 'init':
                from .core import ID, atomic_json
                if len(args.names) != 1 or not ID.fullmatch(args.names[0]):
                    raise Error('modules init requires one lowercase module id')
                name = args.names[0]
                path = m.root/'modules'/name
                path.mkdir(parents=True, exist_ok=False)
                atomic_json(path/'module.json', {'schema': 1, 'id': name, 'version': '0.1.0',
                    'description': 'Describe your module', 'harness': 'codex', 'codex': [m.stock()['version']], 'order': 30,
                    'dependencies': [], 'patches': [], 'tests': [], 'smoke': [],
                    'runtime_env': {'HARNESS_UI_STATE_DIR': str(m.root/'state'/name)}})
                print(path/'module.json')
            elif args.operation == 'add-path':
                for name in args.names:
                    path = str(Path(name).expanduser().resolve())
                    if not Path(path).is_dir():
                        raise Error('scan path must be an existing trusted directory')
                    if path not in m.config['scan_paths']:
                        m.config['scan_paths'].append(path)
                m.save()
            else:
                found = m.modules()
                if args.operation in ('enable', 'disable', 'all'):
                    names = list(found) if args.operation == 'all' else args.names
                    if any(name not in found for name in names):
                        raise Error('unknown module; run modules scan')
                    enabled = set(m.config['enabled'])
                    disabled = set(m.config.get('disabled', []))
                    if args.operation == 'disable':
                        enabled -= set(names); disabled |= set(names)
                    else:
                        enabled |= set(names); disabled -= set(names)
                    m.config.update(enabled=sorted(enabled), disabled=sorted(disabled))
                    selected = set(m.config.get('selected_modules', []))
                    ui_names = {name for name in names if found[name]['patches'] or Path(found[name]['root']).is_relative_to((m.root/'catalog/modules').resolve())}
                    selected = selected-ui_names if args.operation == 'disable' else selected|ui_names
                    m.config['selected_modules'] = sorted(selected); m.save()
                print(json.dumps([{k: v for k, v in mod.items() if k != 'root'} | {'enabled': (name in m.config['enabled'] or m.config['enable_new']) and name not in m.config.get('disabled', [])}
                                  for name, mod in found.items()], indent=2))
        elif args.action == 'notices':
            from .notices import notice
            if args.hours <= 0:
                raise Error('reminder hours must be positive')
            print(json.dumps(notice(m, args.operation, args.hours), indent=2))
        elif args.action == 'config':
            if args.key == 'updates':
                if args.value not in ('auto', 'ask', 'off'):
                    raise Error('updates must be auto, ask or off')
                m.config['updates'] = args.value
            else:
                if args.value not in ('true', 'false'):
                    raise Error('enable-new must be true or false')
                m.config['enable_new'] = args.value == 'true'
            m.save()
        elif args.action == 'update':
            # Ask mode must not fetch executable Git content before consent.
            yes = args.yes
            if m.config['updates'] == 'ask' and not yes:
                from .core import sys_stdin_yes
                yes = sys_stdin_yes()
                if not yes:
                    try:
                        print(json.dumps(m.availability(m.stock() if m.stock_available() else {'version': '*'})))
                    except Error as e:
                        print(str(e), file=sys.stderr)
                    print(json.dumps({'status': 'pending', 'reason': 'trusted update needs approval; run update --yes'}))
                    return 2
            if not args.no_refresh and not args.refreshed:
                with m.lock():
                    refreshed_manager(m)
            print(json.dumps(m.update(yes=yes, refresh=False, refresh_native=not args.no_refresh,
                                      **({'no_build':True} if args.no_build else {})), indent=2))
        elif args.action == 'statusline':
            from .surface import resolve_state
            from . import statusline, claude_statusline
            settings = resolve_state(args.provider, m)
            env = dict(os.environ, **settings['environment'])
            if args.items is not None: env['HARNESS_UI_STATUS_LINE_ITEMS'] = args.items
            if args.ascii is not None: env['HARNESS_UI_STATUS_LINE_ASCII'] = '1' if args.ascii else '0'
            if args.color is not None: env['HARNESS_UI_STATUS_LINE_COLORS'] = '1' if args.color else '0'
            raw = sys.stdin.read()
            try:
                rendered = claude_statusline.render(raw, args.compose_backup, args.columns, settings['state_root'], env) if args.provider == 'claude' else statusline.render('codex', raw, state_root_path=settings['state_root'], columns=args.columns, environ=env)
            except claude_statusline.Error as error:
                raise Error(str(error)) from error
            sys.stdout.write(rendered+('\n' if rendered else ''))
        elif args.action == 'codex':
            from .codex import install_hooks
            print(json.dumps(install_hooks(path=args.path, apply=not args.check, python=args.python), indent=2))
        elif args.action == 'claude' and args.claude_action == 'statusline':
            from . import claude_statusline as native
            from .claude import configure_features
            manifest_path = Path(m.config.get('claude_install', {}).get('source') or m.root/'catalog/adapters/claude-code')/'manifest.json'
            items = [item.strip() for item in args.items.split(',')] if args.items is not None else None
            try:
                if args.operation == 'enable':
                    configure_features({'statusline': True}, manifest_path=manifest_path)
                    result = native.enable(m.root, args.executable or os.environ.get('HARNESS_UI_COMMAND', 'harness-ui'), args.settings, args.mode, items)
                elif args.operation == 'disable':
                    result = native.disable(m.root, args.settings)
                    try:
                        result['collector'] = configure_features({'statusline': False}, manifest_path=manifest_path)
                    except Error as error:
                        result['collector_advisory'] = str(error)
                elif args.operation == 'detect': result = native.detect(args.settings)
                else: result = native.check(args.settings, m.root)
            except native.Error as error:
                raise Error(str(error)) from error
            print(json.dumps(result, indent=2))
        elif args.action == 'claude' and args.claude_action == 'features':
            from .claude import configure_features, feature_definitions
            source = Path(m.config.get('claude_install', {}).get('source') or m.root/'catalog/adapters/claude-code')
            manifest_path = source/'manifest.json'
            definitions = feature_definitions(manifest_path)
            names = [name for name, item in definitions.items() if item.get('enable_all', True) and name not in ('pet', 'mascot', 'animation')] if args.operation == 'all' else args.names
            if not names:
                raise Error('choose at least one feature')
            values = {name: args.operation != 'disable' for name in names}
            plugin = args.plugin or m.config.get('claude_install', {}).get('plugin', 'info-pane@harness-ui-mods')
            if args.operation != 'disable' and m.config.get('claude_install', {}).get('ownership') == 'native' and m.config['claude_install'].get('enabled') is False:
                from .claude import enable_native_plugin
                enable_native_plugin(plugin, args.claude_command)
                m.config['claude_install']['enabled'] = True
            result = configure_features(values, plugin, args.claude_command, manifest_path)
            if 'statusline' in values:
                from . import claude_statusline as native
                try:
                    result['native_statusline'] = native.enable(m.root, os.environ.get('HARNESS_UI_COMMAND', 'harness-ui'), args.settings, args.mode) if values['statusline'] else native.disable(m.root, args.settings)
                except native.Error as error:
                    raise Error(str(error)) from error
            selected = set(m.config.get('selected_modules', m.config.get('enabled', [])))
            selected = selected | set(names) if args.operation != 'disable' else selected-set(names)
            m.config['selected_modules'] = sorted(selected); m.save()
            print(json.dumps(result))
        elif args.action == 'claude':
            from .claude import install
            from .core import sys_stdin_yes
            from .claude import native_ownership
            existing_native = native_ownership(args.claude_command)[0] if not args.dry_run else None
            if args.source is None and not args.refreshed and not args.dry_run and existing_native is None and args.destination is None and args.ownership not in ('legacy','managed'):
                if m.config['updates'] == 'ask' and not args.yes and not sys_stdin_yes():
                    print(json.dumps({'status': 'pending', 'reason': 'trusted catalog fetch needs approval'}))
                    return 2
                with m.lock():
                    refreshed_manager(m)
            if args.source is not None and not args.dry_run:
                raise Error('Local development uses claude --plugin-dir PATH or CLAUDE_CODE_PLUGIN_DIRS; installed plugins use the native marketplace route')
            source = args.source or m.root/'catalog/adapters/claude-code'
            result = install(source, args.destination, args.claude_command, args.adopt_matching, args.ownership, args.dry_run)
            if not args.dry_run:
                m.config['claude_install'] = {'destination': result.get('destination') or (str(args.destination.expanduser().absolute()) if args.destination else ''), 'command': args.claude_command, 'source': str(args.source.resolve()) if args.source else None, 'ownership': result.get('ownership','managed'), 'plugin':result.get('plugin_ref','info-pane@harness-ui-mods'), 'enabled':result.get('enabled')}
                m.save()
            print(json.dumps(result, indent=2))
        elif args.action == 'state' and args.state_action == 'paths':
            from .surface import resolve_state
            print(json.dumps(resolve_state(args.provider, m)))
        elif args.action == 'state':
            from .surface import publish_session
            options = {'manager': m}
            if args.merge:
                import inspect
                parameters = inspect.signature(publish_session).parameters
                if 'merge' not in parameters and not any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
                    raise Error('merged session publishing requires the updated shared publisher')
                options['merge'] = True
            print(json.dumps(publish_session(args.provider, args.session, args.input_file, args.retain_field, args.consume_input, **options)))
        elif args.action == 'surface':
            from .surface import surface
            print(json.dumps(surface(args.kind, args.session, args.text, args.source_anchor, args.id, args.provider, manager=m)))
        elif args.action == 'check':
            result = m.check(args.online)
            catalog = m.root/'catalog'
            result['source_commit'] = None
            if (catalog/'.git').exists():
                from .core import run
                try: result['source_commit'] = run(['git','-C',catalog,'rev-parse','HEAD'],timeout=5).strip()
                except Error: pass
            print(json.dumps(result, indent=2))
        elif args.action == 'launch' and default_harness == 'claude':
            from .surface import resolve_state
            command = m.config.get('claude_install', {}).get('command', 'claude')
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            env = dict(os.environ, **resolve_state('claude', m)['environment'])
            selected_home = str(m.root.resolve())
            env.update(HARNESS_UI_HOME=selected_home, CODEX_UI_HOME=selected_home)
            os.execvpe(command, [command, *arguments], env)
        else:
            # Launch never waits on an interactive consent prompt or compilation.
            stock = m.stock(); current = m.current(stock)
            from .core import atomic_json, read_json
            stamp = m.root/'last-auto-check.json'
            try:
                last_check = read_json(stamp, {})
            except (OSError, ValueError):
                last_check = {}
            checked = last_check.get('checked') if isinstance(last_check, dict) else None
            due = not isinstance(checked, (int, float)) or last_check.get('stock') != stock or time.time()-checked > 6*3600
            if m.config['updates'] == 'auto' and due:
                m.root.mkdir(parents=True, exist_ok=True)
                atomic_json(stamp, {'stock': stock, 'checked': time.time()})
                with (m.root/'updates.log').open('ab') as log:
                    subprocess.Popen([sys.executable, '-m', 'codex_ui_mods.cli', '--home', str(m.root), 'update', '--yes'],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            if current is None:
                print('harness-ui: using stock; run harness-ui update to prepare compatible modules.', file=sys.stderr)
                if m.config['updates'] == 'ask':
                    try:
                        selected = m.selection(stock['version'])
                        if selected:
                            print('harness-ui: compatible local modules ready; run update to approve download/build and re-enable.', file=sys.stderr)
                    except Error:
                        pass
                    if due:
                        m.root.mkdir(parents=True, exist_ok=True); atomic_json(stamp, {'stock': stock, 'checked': time.time()})
                        try:
                            ready = m.availability(stock)
                            if ready['compatible_patch_ready']:
                                print('harness-ui: compatible patch ready; run update to approve download and re-enable.', file=sys.stderr)
                        except Error:
                            pass
            if m.config.get('installed_harnesses'):
                try:
                    from .notices import notice
                    optional = notice(m)['features']
                    if optional:
                        print('harness-ui: new optional features: '+', '.join(optional)+'. See notices; dismiss, remind-later or suppress. Nothing enabled.', file=sys.stderr)
                except Error:
                    pass
            binary, env = m.launch(args.arguments)
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            os.execve(binary, [binary, *arguments], env)
    except KeyboardInterrupt:
        print('Installation cancelled.', file=sys.stderr)
        return 130
    except (Error, OSError, ValueError) as e:
        print('harness-ui: '+str(e), file=sys.stderr)
        return 1
    return 0


def codex_main():
    return main(default_harness='codex')


def claude_main():
    return main(default_harness='claude')


if __name__ == '__main__':
    sys.exit(main())
