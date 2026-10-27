"""Native marketplace installation and feature configuration for Claude Code."""
import json
from pathlib import Path
import re

from .core import Error, run

# Native plugin commands were directly verified at this version.
MINIMUM = (2, 1, 310)


def native_ownership(command='claude', plugin_name='info-pane'):
    try:
        listing = json.loads(run([command, 'plugin', 'list', '--json'], timeout=20))
    except (ValueError, TypeError) as error:
        raise Error('Claude plugin listing returned invalid JSON') from error
    if not isinstance(listing, list):
        raise Error('Claude plugin listing returned an unsupported shape')
    for entry in listing:
        if not isinstance(entry, dict):
            raise Error('Claude plugin listing contains a malformed entry')
        identifier = entry.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+', identifier):
            raise Error('Claude plugin listing contains a malformed plugin id')
        # These two booleans are optional across native/skills-directory entries.
        # Missing flags remain conservatively disabled; present non-booleans are
        # unsupported rather than being coerced to a truthy/falsy value.
        if any(key in entry and type(entry[key]) is not bool for key in ('enabled', 'projectEnabled')):
            raise Error('Claude plugin listing contains a malformed enabled state')
        path = entry.get('installPath')
        if not isinstance(path, str) or not path or any(ord(char) < 32 for char in path) or not Path(path).is_absolute():
            raise Error('Claude plugin listing contains a malformed installPath')
    matches = [entry for entry in listing if entry['id'].split('@', 1)[0] == plugin_name
               and not entry['id'].endswith('@skills-dir')]
    enabled = [entry for entry in matches if entry.get('enabled') is True or entry.get('projectEnabled') is True]
    if len(enabled) > 1:
        raise Error('multiple native info-pane owners are enabled; choose one in Claude before managed installation')
    selected = enabled[0] if enabled else matches[0] if matches else None
    return selected, listing


def install(source=None, destination=None, command='claude', adopt_matching=False, ownership='auto', dry_run=False):
    """Install through Claude's native marketplace cache, never a skills directory.

    Legacy destinations are inspection-only. An existing native owner (including
    an explicitly disabled owner) is preserved; no second plugin is registered.
    """
    if ownership not in ('auto', 'native', 'managed', 'legacy'):
        raise Error('ownership must be auto, native or legacy')
    version_text = run([command, '--version'], timeout=20)
    version = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', version_text)
    if not version or tuple(map(int, version.groups())) < MINIMUM:
        raise Error('verified native plugin support requires Claude Code 2.1.310 or newer')
    native, listing = native_ownership(command)
    legacy = [entry for entry in listing if isinstance(entry, dict) and entry.get('id') == 'info-pane@skills-dir']
    legacy_enabled = any(entry.get('enabled') is True or entry.get('projectEnabled') is True for entry in legacy)
    if native is not None:
        enabled = native.get('enabled') is True or native.get('projectEnabled') is True
        if enabled and legacy_enabled:
            raise Error('native and legacy info-pane owners are both enabled; choose one with claude plugin disable; nothing changed')
        if ownership in ('managed', 'legacy'):
            raise Error('legacy managed-copy installation is deprecated and read-only; existing native plugin preserved')
        return {'status': 'unchanged', 'ownership': 'native', 'plugin_ref': native['id'],
                'destination': native.get('installPath'), 'enabled': enabled, 'discovered': True,
                'changed': False, 'dry_run': dry_run, 'claude_version': '.'.join(version.groups()),
                'advisory': 'Existing native plugin preserved; feature configuration uses its native plugin reference.' if enabled else 'Native plugin remains disabled; no second copy created.'}
    if ownership == 'native':
        raise Error('no native info-pane plugin installed; an existing native ownership record is not silently reinstalled')
    if legacy_enabled and not dry_run:
        raise Error('legacy skills-directory info-pane is loaded; preserve and migrate it explicitly before native installation; nothing copied or replaced')
    if legacy or destination is not None or ownership in ('managed', 'legacy'):
        return {'status': 'unchanged', 'ownership': 'legacy', 'plugin_ref': 'info-pane@skills-dir',
                'destination': legacy[0].get('installPath') if legacy else str(destination) if destination else None,
                'enabled': legacy_enabled, 'changed': False, 'dry_run': dry_run,
                'advisory': 'Legacy destination is read-only and preserved. Development uses --plugin-dir or CLAUDE_CODE_PLUGIN_DIRS; installed plugins use the native marketplace cache.'}
    marketplace = 'rachael/harness-ui-mods'
    if source is not None:
        source = Path(source).resolve()
        catalog = source.parent.parent
        if not (catalog/'.claude-plugin/marketplace.json').is_file():
            raise Error('reviewed local marketplace missing; refresh the trusted catalog before native installation')
        marketplace = str(catalog)
    install_command = [command, 'plugin', 'install', 'info-pane', '--marketplace', marketplace, '--scope', 'user', '--json']
    if dry_run:
        return {'status': 'planned', 'ownership': 'native', 'destination': None, 'dry_run': True,
                'changed': False, 'native_command': install_command}
    if source is not None and Path(source).is_dir():
        run([command, 'plugin', 'validate', '--json', str(source)], timeout=30)
    # No -y/accept-command bypass: this marketplace declares a normal source tree.
    run(install_command, timeout=120)
    installed, _ = native_ownership(command)
    if installed is None or installed['id'] != 'info-pane@harness-ui-mods':
        raise Error('native plugin installation was not confirmed; no fallback copy attempted')
    return {'status': 'installed', 'ownership': 'native', 'plugin_ref': installed['id'],
            'destination': installed.get('installPath'), 'enabled': installed.get('enabled') is True or installed.get('projectEnabled') is True,
            'changed': True, 'new_install': True, 'discovered': True, 'claude_version': '.'.join(version.groups())}


def update_native(command='claude', plugin='info-pane@harness-ui-mods'):
    """Approved update only: preserve owner and disabled state, never reinstall."""
    before, _ = native_ownership(command)
    if before is None or before['id'] != plugin:
        raise Error('native plugin owner is missing or changed; no update or reinstall attempted')
    enabled = before.get('enabled') is True or before.get('projectEnabled') is True
    run([command, 'plugin', 'update', plugin, '--scope', 'user', '--json'], timeout=120)
    after, _ = native_ownership(command)
    if after is None or after['id'] != plugin:
        raise Error('native plugin update owner could not be verified')
    if enabled != (after.get('enabled') is True or after.get('projectEnabled') is True):
        raise Error('native plugin enabled state changed during update; inspect Claude plugin settings')
    return {'status': 'updated', 'ownership': 'native', 'plugin_ref': plugin,
            'enabled': enabled, 'destination': after.get('installPath'), 'reload_required': True}


def feature_definitions(manifest_path=None):
    """Metadata only; the adapter owns feature IDs and native config keys."""
    if manifest_path is not None and Path(manifest_path).is_file():
        data = json.loads(Path(manifest_path).read_text())
        items = data.get('features', []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not all(isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('config_key'), str) for item in items):
            raise Error('invalid adapter feature manifest')
        return {item['id']: item for item in items}
    return {'info-pane': {'config_key': 'infoPane', 'default': True},
            'context-drawer': {'config_key': 'contextDrawer', 'default': True}}


def native_preferences(command='claude', manifest_path=None):
    """Read native saved inputs for chooser display; never configure a plugin."""
    owner, _ = native_ownership(command)
    if owner is None:
        return None
    if not (owner.get('enabled') is True or owner.get('projectEnabled') is True):
        return {'modules': [], 'options': {}}
    report = json.loads(run([command, 'plugin', 'configure', owner['id'], '--json'], timeout=20))
    if not isinstance(report, dict) or not isinstance(report.get('schema'), dict) or not isinstance(report.get('inputs', {}), dict):
        raise Error('Claude plugin configuration returned an unsupported shape')
    inputs = report.get('inputs', {})
    def value(key, default):
        current = inputs.get(key, report['schema'].get(key, {}).get('default', default))
        if isinstance(current, str):
            try: current = json.loads(current)
            except ValueError: pass
        return current
    from .selector import option_definitions
    modules, options = [], {}
    for name, feature in feature_definitions(manifest_path).items():
        enabled = value(feature['config_key'], feature.get('default', True))
        if type(enabled) is not bool:
            raise Error('native saved feature has an unsupported value')
        if enabled: modules.append(name)
        for option in option_definitions(feature):
            if option.get('config_key'):
                options.setdefault(name, {})[option['id']] = value(option['config_key'], option['default'])
    return {'modules': modules, 'options': options}


def configure_features(values, plugin='info-pane@harness-ui-mods', command='claude', manifest_path=None, option_values=None):
    """Use Claude's native userConfig API; omitted options remain untouched."""
    import subprocess
    report = json.loads(run([command, 'plugin', 'configure', plugin, '--json'], timeout=20))
    if not isinstance(report, dict) or not isinstance(report.get('schema'), dict):
        raise Error('Claude plugin configure returned an unsupported shape')
    fields = {name: item['config_key'] for name, item in feature_definitions(manifest_path).items()}
    requested = {}
    for feature, enabled in values.items():
        key = fields.get(feature)
        if feature == 'pet':
            key = next((name for name in ('pet', 'petEnabled') if name in report['schema']), None)
        if key is None or key not in report['schema']:
            raise Error('plugin does not expose feature '+feature+'; existing configuration preserved')
        if type(enabled) is not bool:
            raise Error('feature values must be booleans')
        requested[key] = 'true' if enabled else 'false'
    if option_values:
        from .selector import option_definitions
        definitions = feature_definitions(manifest_path)
        for module_id, chosen in option_values.items():
            if module_id not in definitions or not isinstance(chosen, dict):
                raise Error('invalid native module options')
            available = {item['id']: item for item in option_definitions(definitions[module_id])}
            for option_id, value in chosen.items():
                if option_id not in available: raise Error('unknown native module option')
                option = available[option_id]
                if (option['type'] == 'boolean' and type(value) is not bool) or (option['type'] == 'choice' and value not in option['choices']):
                    raise Error('invalid native module option value')
                key = option.get('config_key')
                if key is None: continue
                if key not in report['schema']: raise Error('native option is not exposed by this plugin')
                requested[key] = ('true' if value else 'false') if type(value) is bool else value
    if not requested:
        return {'plugin': plugin, 'features': values, 'verified': True, 'changed': False}
    try:
        result = subprocess.run([command, 'plugin', 'configure', plugin, '--values-stdin'],
                                input=json.dumps(requested), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Error('Claude plugin configuration failed: '+str(e)) from e
    if result.returncode:
        raise Error('Claude plugin configuration failed: '+result.stderr[-1000:])
    saved = json.loads(run([command, 'plugin', 'configure', plugin, '--json'], timeout=20))
    if not isinstance(saved, dict) or not isinstance(saved.get('inputs'), dict) or any(str(saved['inputs'].get(key)).lower() != value for key, value in requested.items()):
        raise Error('Claude plugin configuration read-back failed')
    return {'plugin': plugin, 'features': values, 'verified': True}


def enable_native_plugin(plugin, command='claude'):
    """Only an explicit feature-enable action calls this, never default migration."""
    run([command, 'plugin', 'enable', plugin, '--json'], timeout=30)
    native, _ = native_ownership(command)
    if native is None or native['id'] != plugin or not (native.get('enabled') is True or native.get('projectEnabled') is True):
        raise Error('native plugin enable was not confirmed; no managed copy attempted')
    return True
