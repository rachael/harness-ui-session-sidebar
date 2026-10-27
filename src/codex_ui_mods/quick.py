"""One-command defaults for installed harnesses; no inference or pet changes."""
from pathlib import Path
import os
import shutil

from .core import Error
from .claude import install as install_claude


def prepare(manager, harness=None, stock=None, updates=None, claude_command='claude', claude_destination=None, modules=None, all_modules=False, dry_run=False, mode='auto', module_options=None, claude_settings=None, module_changes=None, no_build=False, option_changes=None, replace_selection=False, component_scope=None):
    stock = stock or shutil.which('codex')
    first_install = not manager.config.get('installed_harnesses')
    previous = set(manager.config.get('installed_harnesses', []))
    codex = harness in (None, 'all', 'codex') and bool(stock)
    claude = harness in (None, 'all', 'claude') and bool(shutil.which(claude_command))
    if harness is None and not first_install:
        codex = codex and 'codex' in previous
        claude = claude and 'claude' in previous
    if harness == 'codex' and not codex:
        raise Error('stock Codex not found')
    if harness == 'claude' and not claude:
        raise Error('Claude Code not found')
    if not codex and not claude:
        raise Error('install Codex or Claude Code first, then rerun harness-ui install')
    policy = updates or (manager.config['updates'] if manager.config_path.exists() else 'ask')
    if dry_run:
        return {'dry_run': True, 'updates': policy, 'harnesses': [name for name, selected in (('codex', codex), ('claude', claude)) if selected],
                'modules': modules if modules is not None else 'all current' if all_modules else 'preserve existing' if not first_install else 'catalog defaults',
                'claude_destination': str(claude_destination) if claude_destination else 'Claude native plugin cache', 'pet_changes': False, 'no_build': no_build,
                'selection_mode': 'replace' if replace_selection else 'additive', 'statusline_mode': mode, 'module_options': module_options or manager.config.get('module_options', {}), 'preparation': 'verified compatible bundle or source build; catalog metadata is not fetched by dry-run'}
    if modules is not None:
        from .claude import feature_definitions
        available = set()
        if codex:
            available |= {name for name, item in manager.modules().items() if item.get('harness','codex') == 'codex' and (item['patches'] or Path(item['root']).is_relative_to((manager.root/'catalog/modules').resolve()))}
        if claude:
            available |= set(feature_definitions(manager.root/'catalog/adapters/claude-code/manifest.json'))
        if not set(modules) <= available:
            raise Error('selected module is not available for the selected installed harnesses')
    previous_selection = set(manager.config.get('selected_modules', manager.config.get('enabled', [])))
    recorded_components = manager.config.get('included_components', {})
    if not isinstance(recorded_components, dict) or any(not isinstance(values, list) or not all(isinstance(value, str) for value in values) for values in recorded_components.values()):
        raise Error('included_components must map harness names to component ID lists')
    previous_components = {name: set(recorded_components.get(name, previous_selection if name in previous else [])) for name in ('codex', 'claude')}
    component_records = {name: sorted(values) for name, values in previous_components.items()}
    selection_after = set(previous_selection)
    requested_components = set(component_scope if component_scope is not None else modules or [])
    already_included = set(requested_components)
    manager.config['updates'] = policy
    if module_options is not None:
        saved_options = {name: dict(values) for name, values in manager.config.get('module_options', {}).items()}
        for name, values in module_options.items(): saved_options.setdefault(name, {}).update(values)
        manager.config['module_options'] = saved_options
    if mode != 'auto' or first_install:
        manager.config.setdefault('statusline_options', {})['mode'] = mode
    result = {'updates': policy, 'harnesses': {}, 'selection': {'mode': 'replace' if replace_selection else 'additive', 'requested': sorted(requested_components)}}
    if mode not in ('auto', 'extend', 'replace'):
        raise Error('statusline mode must be auto, extend or replace')
    if codex:
        manager.config['stock'] = stock
        manager.stock()
        builtins = manager.root/'catalog/modules'
        discovered = manager.modules()
        builtin_ids = {name for name, item in discovered.items() if item.get('harness', 'codex') == 'codex' and Path(item['root']).is_relative_to(builtins.resolve())}
        ids = sorted(builtin_ids | {name for name, item in discovered.items() if item.get('harness', 'codex') == 'codex' and item['patches']})
        from .claude import feature_definitions
        claude_ids = set(feature_definitions(manager.root/'catalog/adapters/claude-code/manifest.json'))
        selected = set(modules) & set(ids) if modules is not None else set(ids) if all_modules else {name for name in builtin_ids if discovered[name].get('default', True) or ('codex' not in previous and name == 'statusline')}
        current = set(manager.config['enabled']) - set(manager.config.get('disabled', []))
        already_included -= (requested_components & set(ids)) - (current | previous_components['codex'])
        if modules is not None:
            changed = set(ids) if replace_selection else (set(module_changes) | (requested_components-previous_components['codex']-set(manager.config.get('disabled', [])))) & set(ids) if module_changes is not None else selected - set(manager.config.get('disabled', [])) - (previous_components['codex']-current)
            manager.config['enabled'] = sorted((set(manager.config['enabled'])-changed) | (selected & changed))
            manager.config['disabled'] = sorted((set(manager.config.get('disabled', [])) | (changed-selected))-(selected & changed))
        elif 'codex' not in previous or all_modules:
            manager.config['enabled'] = sorted(set(manager.config['enabled']) | (selected-set(manager.config.get('disabled', []))))
        selection_after |= set(manager.config['enabled'])

        manager.save()
        # Claude has its own selected-harness branch below. Preparing Codex must
        # not inspect or update an unrelated existing Claude installation.
        result['harnesses']['codex'] = manager.update(yes=True, refresh=False, include_claude=False,
                                                     **({'no_build':True} if no_build else {}))
        if (set(manager.config['enabled']) - set(manager.config.get('disabled', []))) & {'info-pane', 'session-sidebar'}:
            from .codex import install_hooks
            result['harnesses']['codex']['hooks'] = install_hooks()
    if claude:
        destination = Path(claude_destination) if claude_destination is not None else None
        result['harnesses']['claude'] = install_claude(manager.root/'catalog/adapters/claude-code', destination, claude_command)
        from .claude import configure_features, feature_definitions, native_preferences
        manifest_path = manager.root/'catalog/adapters/claude-code/manifest.json'
        definitions = feature_definitions(manifest_path)
        install_result = result['harnesses']['claude']
        plugin_ref = install_result.get('plugin_ref', 'info-pane@harness-ui-mods')
        native_existing = install_result.get('ownership') == 'native' and not install_result.get('new_install')
        disabled = set(manager.config.get('disabled', []))
        preferences = native_preferences(claude_command, manifest_path) if native_existing and install_result.get('enabled') and (modules is not None or all_modules) else None
        # An existing native owner first seen here already supplies the full plugin.
        # Preserve its off values; component-origin installs retain their narrower scope.
        if native_existing and 'claude' not in previous and not previous_components['claude']:
            previous_components['claude'] = set(definitions)
        included = previous_components['claude'] | set(preferences['modules'] if preferences else [])
        already_included -= (requested_components & set(definitions)) - included
        values = {}
        if modules is not None:
            changed = set(definitions) if replace_selection or install_result.get('new_install') else (set(module_changes) | (requested_components-included-disabled)) & set(definitions) if module_changes is not None else set(modules) - included - disabled
            values = {name: name in modules for name in changed if name in definitions and name not in ('pet', 'mascot', 'animation')}
        elif all_modules:
            values = {name: item.get('enable_all', True) and name not in disabled for name, item in definitions.items() if name not in ('pet', 'mascot', 'animation')}
        elif 'claude' not in previous and not native_existing and install_result.get('ownership') != 'legacy' and 'statusline' in definitions and 'statusline' not in disabled:
            values = {'statusline': True}
        if install_result.get('new_install') and modules is None:
            values.update({name: False for name in definitions if name in disabled})
        active_native = (set(modules) | included) if modules is not None and not replace_selection else set(modules) if modules is not None else {name for name, enabled in values.items() if enabled} if all_modules else {name for name, item in definitions.items() if item.get('default', True) or name == 'statusline'} if 'claude' not in previous else set(previous_selection)
        if module_changes is not None: active_native -= set(module_changes)-set(modules or [])
        if modules is None: active_native -= disabled
        requested_options = (option_changes or {}) if native_existing else module_options
        native_options = {name: dict(chosen_options) for name, chosen_options in (requested_options or {}).items() if name in definitions and name in active_native}
        statusline_change = values.get('statusline')
        if preferences:
            values = {name: enabled for name, enabled in values.items() if (name in preferences['modules']) != enabled}
            native_options = {name: {key: value for key, value in options.items() if preferences['options'].get(name, {}).get(key) != value} for name, options in native_options.items()}
            native_options = {name: options for name, options in native_options.items() if options}
        if native_existing and not install_result.get('enabled') and (replace_selection or module_changes) and any(values.values()):
            from .claude import enable_native_plugin
            enable_native_plugin(plugin_ref, claude_command)
            install_result['enabled'] = True
        if (install_result.get('ownership') == 'native' and not install_result.get('enabled')) or install_result.get('ownership') == 'legacy':
            values = {}; native_options = {}; statusline_change = None
        if values or native_options:
            result['harnesses']['claude']['features'] = configure_features(values, plugin=plugin_ref, command=claude_command, manifest_path=manifest_path, option_values=native_options)
        if 'statusline' in definitions and install_result.get('ownership') != 'legacy' and (install_result.get('ownership') != 'native' or install_result.get('enabled')):
            from . import claude_statusline as native
            try:
                if statusline_change is True:
                    result['harnesses']['claude']['statusline'] = native.enable(manager.root, executable=os.environ.get('HARNESS_UI_COMMAND') or shutil.which('harness-ui') or 'harness-ui', mode=mode, settings=claude_settings)
                elif statusline_change is False:
                    result['harnesses']['claude']['statusline'] = native.disable(manager.root, settings=claude_settings)
                elif mode != 'auto':
                    result['harnesses']['claude']['statusline'] = native.set_mode(manager.root, mode, settings=claude_settings)
                else:
                    result['harnesses']['claude']['statusline'] = native.check(home=manager.root, settings=claude_settings)
            except native.Error as error:
                raise Error(str(error)) from error
        if modules is not None:
            changed = set(definitions) if replace_selection else (set(module_changes) | (requested_components-included-disabled)) & set(definitions) if module_changes is not None else (set(modules) & set(definitions)) - included - disabled
            manager.config['disabled'] = sorted((set(manager.config.get('disabled', [])) | (changed-set(modules))) - (changed & set(modules)))
        manager.config['claude_install'] = {'destination': install_result.get('destination') or (str(destination.expanduser().absolute()) if destination else ''), 'command': claude_command, 'source': None, 'ownership': install_result.get('ownership','managed'), 'plugin': plugin_ref, 'enabled': install_result.get('enabled')}
    if modules is not None:
        if replace_selection:
            selection_after = (selection_after-available) | set(modules)
        else:
            seen = set().union(*(previous_components[name] for name, present in (('codex', codex), ('claude', claude)) if present))
            selection_after |= set(modules) - set(manager.config.get('disabled', [])) - (seen-previous_selection)
            selection_after -= set(module_changes or [])-set(modules)
        if codex: component_records['codex'] = sorted(previous_components['codex'] | (requested_components & set(ids)))
        if claude: component_records['claude'] = sorted(previous_components['claude'] | (requested_components & set(definitions)))
        result['selection']['already_included'] = sorted(already_included)
    elif first_install or all_modules or (codex and 'codex' not in previous) or (claude and 'claude' not in previous):
        selection_after |= set(manager.config.get('enabled', []))
        if claude:
            selection_after |= {name for name, item in definitions.items() if (values.get(name, item.get('default', True)) or (name == 'statusline' and 'claude' not in previous)) and name not in manager.config.get('disabled', [])}
    if modules is None:
        if codex: component_records['codex'] = sorted(previous_components['codex'] | set(ids))
        if claude: component_records['claude'] = sorted(previous_components['claude'] | set(definitions))
    manager.config['included_components'] = component_records
    manager.config['selected_modules'] = sorted(selection_after)
    installed = set(previous) | set(result['harnesses'])
    manager.config['installed_harnesses'] = sorted(installed)
    from .notices import baseline, notice
    if first_install:
        baseline(manager)
    else:
        result['optional_features'] = notice(manager)['features']
    manager.save()
    return result
