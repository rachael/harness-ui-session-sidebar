"""Claude Code's native statusLine slot: render, compose, and scoped enable/disable.

The slot is the person's. Enabling keeps their existing statusLine object byte-exact in
a backup and extends it: their command's output first and unchanged (same stdin, their
shell semantics, a bounded timeout), then our extras on a line after it. Disabling restores the backup only when
the slot still holds exactly what enabling wrote, so later edits of theirs are never
overwritten. Every other settings key is left as it was.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from . import statusline

MARKER = 'statusline render --provider claude'
COMPOSE_TIMEOUT = 2.0
# Composing a line the person keeps: by default leave out what such lines usually show
# already (folder, model, context), so nothing appears twice. Their choice of items wins.
COMPOSE_ITEMS = ('effort', 'tier', 'limits', 'session', 'cache')


class Error(Exception):
    pass


def settings_path(environ=None):
    env = os.environ if environ is None else environ
    base = env.get('CLAUDE_CONFIG_DIR') or str(Path(env.get('HOME') or Path.home())/'.claude')
    return Path(base).expanduser()/'settings.json'


def backup_path(home):
    return Path(home)/'claude-statusline.json'


def _read_json(path, default=None):
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return default, None
    try:
        value = json.loads(text)
    except ValueError as e:
        raise Error(f'{path} is not valid JSON; nothing changed') from e
    return value, text


def _write_json(path, value, like=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    indent = 2
    if like:
        for line in like.splitlines()[1:]:
            stripped = line.lstrip(' ')
            if stripped and stripped != line:
                indent = len(line) - len(stripped)
                break
    text = json.dumps(value, indent=indent, ensure_ascii=False) + '\n'
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        if path.exists():
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def ours(status_line):
    return isinstance(status_line, dict) and status_line.get('type') == 'command' and MARKER in str(status_line.get('command', ''))


def command_for(executable, home):
    """Our statusLine object: an argv quoted for the shell Claude runs it in. `--home` comes
    first, so the renderer reads the same manager configuration (data modules, state root)
    as the install that wrote it."""
    argv = [executable, '--home', str(home), 'statusline', 'render', '--provider', 'claude', '--compose-backup', str(backup_path(home))]
    return {'type': 'command', 'command': ' '.join(shlex.quote(a) for a in argv)}


def check(settings=None, home=None, environ=None):
    path = Path(settings) if settings else settings_path(environ)
    data, _ = _read_json(path, {})
    current = data.get('statusLine') if isinstance(data, dict) else None
    backup, _ = _read_json(backup_path(home), None) if home else (None, None)
    if ours(current):
        state = 'enabled' if backup and backup.get('installed') == current else 'enabled-unmanaged'
    elif backup:
        state = 'modified'
    else:
        state = 'custom' if current else 'absent'
    return {'settings': str(path), 'state': state, 'mode': backup.get('mode') if backup else None}


def detect(settings=None, environ=None):
    """What the slot holds and the mode `auto` picks: a custom command is extended (its
    lines kept, extras added); an empty slot gets the full layout."""
    path = Path(settings) if settings else settings_path(environ)
    data, _ = _read_json(path, {})
    current = data.get('statusLine') if isinstance(data, dict) else None
    existing = 'ours' if ours(current) else 'custom' if current is not None else 'none'
    return {'settings': str(path), 'existing': existing, 'auto': 'extend' if existing == 'custom' else 'replace'}


def enable(home, executable='harness-ui', settings=None, mode='auto', items=None, environ=None):
    """Install our renderer in the slot.

    mode 'extend': keep the existing command's lines first, add a line of extras.
    mode 'replace': our full layout alone; the existing object is still backed up and
    `disable` puts it back. mode 'auto': extend a custom command, else replace. An
    existing custom line is only ever set aside by an explicit 'replace'. Running it
    again with the same choice changes nothing.
    """
    if mode not in ('auto', 'extend', 'replace'):
        raise Error('mode must be auto, extend or replace')
    path = Path(settings) if settings else settings_path(environ)
    data, text = _read_json(path, {})
    if not isinstance(data, dict):
        raise Error(f'{path} does not hold a JSON object; nothing changed')
    current = data.get('statusLine')
    backup = backup_path(home)
    saved, _ = _read_json(backup, None)
    if ours(current):
        if not isinstance(saved, dict) or saved.get('installed') != current:
            return {'state': 'enabled-unmanaged', 'changed': False, 'mode': None}
        mode = saved['mode'] if mode == 'auto' else mode
        if mode == 'extend' and not saved.get('present'):
            mode = 'replace'
        if items is not None:
            items = [i for i in items if i in statusline.KNOWN_ITEMS]
        wanted_items = items if items is not None else (saved.get('items') if mode == saved.get('mode') else list(COMPOSE_ITEMS) if mode == 'extend' else None)
        # An earlier install's command (one without --home, say) is brought up to date in
        # place: the slot still holds exactly what we wrote, so it is ours to rewrite.
        installed = command_for(executable, home)
        if (mode, wanted_items, installed) == (saved.get('mode'), saved.get('items'), current):
            return {'state': 'enabled', 'changed': False, 'mode': mode}
        if installed != current:
            updated = dict(data)
            updated['statusLine'] = installed
            _write_json(backup, dict(saved, mode=mode, items=wanted_items, installed=installed))
            try:
                _write_json(path, updated, like=text)
            except BaseException:
                # the slot still holds the old command: the backup goes back to match it
                _write_json(backup, saved)
                raise
        else:
            _write_json(backup, dict(saved, mode=mode, items=wanted_items))
        return {'state': 'enabled', 'changed': True, 'mode': mode}
    if saved is not None:
        raise Error(f'{backup} is left from an earlier enable whose slot was edited since; disable or remove it first')
    if mode == 'auto':
        mode = 'extend' if current is not None else 'replace'
    if mode == 'extend' and current is None:
        mode = 'replace'
    if items is not None:
        items = [i for i in items if i in statusline.KNOWN_ITEMS]
    elif mode == 'extend':
        items = list(COMPOSE_ITEMS)
    installed = command_for(executable, home)
    _write_json(backup, {'schema': 1, 'settings': str(path), 'present': current is not None, 'mode': mode,
                         'original': deepcopy(current), 'installed': installed, 'items': items})
    updated = dict(data)
    updated['statusLine'] = installed
    try:
        _write_json(path, updated, like=text)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return {'state': 'enabled', 'changed': True, 'mode': mode}


def set_mode(home, mode, items=None, settings=None, environ=None):
    """Change mode or items of an enabled slot only. A slot that is not ours stays as it
    is: choosing a mode never enables the status line."""
    state = check(settings, home, environ)['state']
    if state != 'enabled':
        return {'state': state, 'changed': False, 'mode': None}
    if mode == 'auto':
        raise Error('set_mode takes extend or replace')
    return enable(home, settings=settings, mode=mode, items=items, environ=environ)


def disable(home, settings=None, environ=None):
    """Put back exactly what enabling replaced, unless the slot was edited since."""
    backup_file = backup_path(home)
    backup, _ = _read_json(backup_file, None)
    if not backup:
        return {'state': check(settings, home, environ)['state'], 'changed': False}
    path = Path(settings or backup.get('settings') or settings_path(environ))
    data, text = _read_json(path, {})
    if not isinstance(data, dict):
        raise Error(f'{path} does not hold a JSON object; nothing changed')
    if data.get('statusLine') != backup.get('installed'):
        raise Error('the statusLine was edited after enable; nothing restored (backup kept at ' + str(backup_file) + ')')
    updated = dict(data)
    if backup.get('present'):
        updated['statusLine'] = backup['original']
    else:
        updated.pop('statusLine', None)
    _write_json(path, updated, like=text)
    backup_file.unlink()
    return {'state': 'custom' if backup.get('present') else 'absent', 'changed': True}


def compose(stdin_text, backup_file, timeout=COMPOSE_TIMEOUT):
    """The person's own statusLine output, run as Claude runs it: their command string in
    the shell, the same stdin, bounded. Nothing when absent, ours, failing or slow."""
    backup, _ = _read_json(backup_file, None)
    if not isinstance(backup, dict) or backup.get('mode', 'extend') != 'extend':
        return ''
    original = backup.get('original')
    if not isinstance(original, dict) or original.get('type') != 'command' or ours(original):
        return ''
    command = original.get('command')
    if not isinstance(command, str) or not command.strip():
        return ''
    try:
        # Deliberately the shell: this is the command the person configured for Claude
        # Code, which runs it the same way. Nothing of ours is interpolated into it.
        out = subprocess.run(command, shell=True, input=stdin_text, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return out.stdout.rstrip('\n')


def render(stdin_text, compose_backup=None, columns=None, state_root=None, environ=None):
    """The person's own lines first and unchanged, then ours (extension); ours alone
    when there is nothing to extend.
    Items: HARNESS_UI_STATUS_LINE_ITEMS when set, else those chosen at enable."""
    env = os.environ if environ is None else environ
    backup, _ = _read_json(compose_backup, None) if compose_backup else (None, None)
    chosen = backup.get('items') if isinstance(backup, dict) else None
    items = None if 'HARNESS_UI_STATUS_LINE_ITEMS' in env or not isinstance(chosen, list) else chosen
    cfg = statusline.config(env, items=items)
    line = statusline.render('claude', stdin_text, state_root_path=state_root, columns=columns, cfg=cfg, environ=env)
    theirs = compose(stdin_text, compose_backup) if compose_backup else ''
    return '\n'.join(part for part in (theirs, line) if part)
