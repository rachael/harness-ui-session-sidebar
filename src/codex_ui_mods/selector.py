"""One-screen terminal module chooser. Metadata is data, never executable code."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import select
import shutil
import sys
import termios
import tty

from .core import Error
from .claude import feature_definitions

KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')


def option_definitions(module):
    options = module.get('options', [])
    if not isinstance(options, list):
        raise Error('module options must be an array')
    result = []
    seen = set()
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get('id'), str) or not KEY.fullmatch(option['id']) or option['id'] in seen:
            raise Error('option requires a unique simple id')
        seen.add(option['id'])
        kind = option.get('type')
        default = option.get('default')
        if kind == 'boolean':
            if type(default) is not bool: raise Error('boolean option requires boolean default')
        elif kind == 'choice':
            choices = option.get('choices')
            if not isinstance(choices, list) or not choices or not all(isinstance(v, str) for v in choices) or default not in choices or len(choices) != len(set(choices)):
                raise Error('choice option requires unique strings and a matching default')
        else:
            raise Error('unsupported option type; use boolean or choice')
        env = option.get('runtime_env')
        if env is not None and (not isinstance(env, str) or not re.fullmatch(r'(?:HARNESS_UI_|CODEX_UI_)[A-Z0-9_]+', env)):
            raise Error('option runtime_env must be a neutral or legacy UI variable')
        config = option.get('config_key')
        if config is not None and (not isinstance(config, str) or not KEY.fullmatch(config)):
            raise Error('invalid native option config_key')
        result.append(option)
    return result


def catalog(manager, harness=None):
    rows = {}
    for name, module in manager.modules().items():
        if harness == 'claude' or module.get('harness', 'codex') != 'codex':
            continue
        if not module['patches'] and not Path(module['root']).is_relative_to((manager.root/'catalog/modules').resolve()):
            continue
        rows[name] = {'id': name, 'label': module.get('name', name), 'default': module.get('default', True),
                      'options': option_definitions(module)}
    if harness != 'codex':
        path = Path(manager.config.get('claude_install', {}).get('source') or manager.root/'catalog/adapters/claude-code')/'manifest.json'
        for name, feature in feature_definitions(path).items():
            options = option_definitions(feature)
            if name in rows:
                merged = {item['id']: item for item in rows[name]['options']}
                for item in options:
                    previous = merged.get(item['id'])
                    if previous and any(previous.get(key) != item.get(key) for key in ('type','default','choices')):
                        raise Error('cross-harness option definitions disagree')
                    merged[item['id']] = dict(previous or {}, **item)
                rows[name]['options'] = list(merged.values())
            else:
                rows[name] = {'id': name, 'label': feature.get('name', name), 'default': feature.get('default', True), 'options': options}
    return sorted(rows.values(), key=lambda row: row['id'])


def defaults(manager, rows, requested=None, all_modules=False, replace_selection=False, harness=None):
    if requested is not None:
        current = set(manager.config.get('enabled', [])) if harness == 'codex' else set(manager.config.get('selected_modules', manager.config.get('enabled', []))) | set(manager.config.get('enabled', []))
        records = manager.config.get('included_components', {})
        included = set(records.get(harness, [])) if harness in ('codex', 'claude') else set().union(*(set(values) for values in records.values()))
        disabled = set(manager.config.get('disabled', []))
        return set(requested) if replace_selection else (set(requested)-disabled-(included-current)) | ((current-disabled) & {row['id'] for row in rows})
    if all_modules: return {row['id'] for row in rows}-set(manager.config.get('disabled', []))
    if manager.config.get('installed_harnesses'):
        chosen = set(manager.config.get('selected_modules', manager.config.get('enabled', [])))
        if manager.config.get('claude_install'):
            from .claude_statusline import check
            if check(home=manager.root)['state'] != 'enabled': chosen.discard('statusline')
        return chosen & {row['id'] for row in rows}-set(manager.config.get('disabled', []))
    return {row['id'] for row in rows if row['default'] or row['id'] == 'statusline'}-set(manager.config.get('disabled', []))


@contextmanager
def terminal(fd):
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def read_key(fd):
    first = os.read(fd, 1)
    if not first: return 'cancel'
    if first == b'\x1b':
        rest = b''
        while select.select([fd], [], [], .04)[0] and len(rest) < 4:
            rest += os.read(fd, 1)
            if rest in (b'[A', b'[B', b'[C', b'[D'): break
        return {b'[A':'up', b'[B':'down', b'[C':'right', b'[D':'left'}.get(rest, 'cancel')
    return {b'\r':'accept', b'\n':'accept', b' ':'space', b'\x03':'cancel'}.get(first, '')


def clean(value):
    return ''.join(c for c in str(value) if ord(c) >= 32 and ord(c) != 127)


def choose(rows, selected, saved_options=None, key_reader=None, output=None):
    """Accept is focused first. Right expands typed options, Enter installs."""
    output = output or sys.stdout
    selected = set(selected)
    initial = set(selected)
    options = {row['id']: {item['id']: (saved_options or {}).get(row['id'], {}).get(item['id'], item['default'])
                          for item in row['options']} for row in rows}
    for row in rows:
        for item in row['options']:
            value = options[row['id']][item['id']]
            if (item['type'] == 'boolean' and type(value) is not bool) or (item['type'] == 'choice' and value not in item['choices']):
                raise Error('saved module option has an invalid value')
    initial_options = json.loads(json.dumps(options))
    expanded = set()
    focus = 0
    def visible():
        values = [(None, None)]
        for row in rows:
            values.append((row, None))
            if row['id'] in expanded:
                values.extend((row, item) for item in row['options'])
        return values
    def paint():
        output.write('\x1b[2J\x1b[H')
        for index, (row, option) in enumerate(visible()):
            marker = '› ' if index == focus else '  '
            if row is None:
                line = '[ Enter ] Accept and continue'
            elif option is None:
                line = ('[x] ' if row['id'] in selected else '[ ] ')+clean(row['label'])
                if row['options']: line += '  '+('▾' if row['id'] in expanded else '▸')
            else:
                value = options[row['id']][option['id']]
                line = '    '+clean(option.get('label', option['id']))+': '+clean('on' if value is True else 'off' if value is False else value)
            output.write(marker+line+'\n')
        output.write('\n↑↓ move   Space select   → options   Enter install\n')
        output.flush()
    while True:
        paint()
        key = key_reader() if key_reader else read_key(sys.stdin.fileno())
        values = visible()
        row, option = values[focus]
        if key == 'cancel': raise Error('Installation cancelled; no module choices changed')
        if key == 'accept':
            output.write('\n')
            return {'modules': sorted(selected), 'options': options,
                    'changed': selected != initial or options != initial_options,
                    'module_changes': sorted(selected ^ initial),
                    'option_changes': {name: {key: value for key, value in values.items() if value != initial_options[name][key]} for name, values in options.items() if values != initial_options[name]}}
        if key == 'up': focus = max(0, focus-1)
        elif key == 'down': focus = min(len(values)-1, focus+1)
        elif key == 'right' and row and row['options']: expanded.add(row['id'])
        elif key == 'left' and row:
            expanded.discard(row['id'])
            focus = next(i for i, (candidate, item) in enumerate(visible()) if candidate and candidate['id']==row['id'] and item is None)
        elif key == 'space' and row:
            if option is None:
                selected.symmetric_difference_update({row['id']})
            elif option['type'] == 'boolean':
                options[row['id']][option['id']] = not options[row['id']][option['id']]
            else:
                choices = option['choices']; value = options[row['id']][option['id']]
                options[row['id']][option['id']] = choices[(choices.index(value)+1)%len(choices)]


def interactive(manager, harness=None, requested=None, all_modules=False, saved_options=None, replace_selection=False):
    rows = catalog(manager, harness)
    selected = defaults(manager, rows, requested, all_modules, replace_selection, harness)
    with terminal(sys.stdin.fileno()):
        return choose(rows, selected, manager.config.get('module_options', {}) if saved_options is None else saved_options)


def parse_options(rows, assignments, saved=None):
    result = json.loads(json.dumps(saved or {}))
    available = {row['id']: {option['id']: option for option in row['options']} for row in rows}
    for assignment in assignments:
        key, separator, raw = assignment.partition('=')
        module, dot, option_id = key.partition('.')
        if not separator or not dot or module not in available or option_id not in available[module]:
            raise Error('option must name a declared MODULE.OPTION=value')
        option = available[module][option_id]
        try: value = json.loads(raw)
        except ValueError: value = raw
        if (option['type'] == 'boolean' and type(value) is not bool) or (option['type'] == 'choice' and value not in option['choices']):
            raise Error('invalid module option value')
        result.setdefault(module, {})[option_id] = value
    return result
