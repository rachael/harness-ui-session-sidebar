"""Explicit, deduplicated pane content with read-back confirmation.

Raw JSON is supported directly. Provenance envelopes require a configured trusted
publisher; this module never rewrites an envelope or executes commands from state.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .core import Error, atomic_json

SAFE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')


def state_root():
    return Path(os.environ.get('HARNESS_UI_STATE_DIR', os.environ.get('CODEX_UI_STATE_DIR',
                str(Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'harness-ui-mods')))).expanduser()


def resolve_state(provider='codex', manager=None):
    """Resolve trusted data modules without invoking a harness or compiler."""
    if not isinstance(provider, str) or not SAFE.fullmatch(provider):
        raise Error('invalid provider identifier')
    if manager is None:
        from .core import Manager
        manager = Manager()
    modules = manager.modules()
    enabled = set(modules if manager.config.get('enable_new') else manager.config.get('enabled', []))
    enabled -= set(manager.config.get('disabled', []))
    aliases = {'HARNESS_UI_STATE_DIR': 'state', 'CODEX_UI_STATE_DIR': 'state',
               'HARNESS_UI_PUBLISH_COMMAND': 'publisher', 'CODEX_UI_PUBLISH_COMMAND': 'publisher',
               'HARNESS_UI_BADGE': 'badge', 'HARNESS_UI_BADGES': 'badges'}
    values = {}
    from .core import select
    chosen = select(modules, sorted(enabled), version=None, disabled=manager.config.get('disabled', []))
    for module in chosen:
        if module['patches']:
            continue
        for key, value in module.get('runtime_env', {}).items():
            if key not in aliases:
                continue
            semantic = aliases[key]
            if semantic == 'state':
                expanded = Path(value).expanduser()
                if not expanded.is_absolute():
                    raise Error('data module state root must be absolute')
                value = str(expanded)
            elif semantic in ('publisher', 'badges'):
                try:
                    value = json.dumps(json.loads(value), sort_keys=True)
                except ValueError as e:
                    raise Error('invalid data module '+key) from e
            if semantic in values and values[semantic] != value:
                raise Error('conflicting enabled data modules for '+semantic)
            values[semantic] = value
    environment = dict(os.environ)
    pairs = [('state', 'HARNESS_UI_STATE_DIR', 'CODEX_UI_STATE_DIR'),
             ('publisher', 'HARNESS_UI_PUBLISH_COMMAND', 'CODEX_UI_PUBLISH_COMMAND'),
             ('badge', 'HARNESS_UI_BADGE', None), ('badges', 'HARNESS_UI_BADGES', None)]
    for semantic, neutral, legacy in pairs:
        explicit = os.environ.get(neutral, os.environ.get(legacy) if legacy else None)
        effective = explicit if explicit is not None else values.get(semantic)
        if effective is not None:
            environment[neutral] = effective
            if legacy: environment[legacy] = effective
    root = Path(environment.get('HARNESS_UI_STATE_DIR', environment.get('CODEX_UI_STATE_DIR',
                str(Path(environment.get('XDG_STATE_HOME', Path.home()/'.local/state'))/'harness-ui-mods')))).expanduser()
    if not root.is_absolute():
        raise Error('state root must be absolute')
    public_env = {key: environment[key] for key in aliases if key in environment}
    public_env['HARNESS_UI_STATE_DIR'] = public_env['CODEX_UI_STATE_DIR'] = str(root)
    return {'provider': provider, 'state_root': str(root),
            'publisher_command': publisher_command(provider, '{session}', environment), 'environment': public_env}


def session_path(root, provider, session):
    if not isinstance(provider, str) or not isinstance(session, str) or not SAFE.fullmatch(provider) or not SAFE.fullmatch(session):
        raise Error('invalid provider/session identifier')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root/'sessions'/provider/(session+'.json')
    if not path.resolve().is_relative_to(root.resolve()):
        raise Error('surface state path escapes configured root')
    return path


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.surface.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Error('another surface update is running; retry') from e
        yield


def badge_for(provider, environ=None):
    environ = os.environ if environ is None else environ
    badges = {'codex': 'C', 'claude': '✳'}
    configured = environ.get('HARNESS_UI_BADGES')
    if configured:
        try:
            registry = json.loads(configured)
        except ValueError as e:
            raise Error('HARNESS_UI_BADGES must be a JSON object') from e
        if not isinstance(registry, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in registry.items()):
            raise Error('HARNESS_UI_BADGES must map provider names to strings')
        badges.update(registry)
    badge = environ.get('HARNESS_UI_BADGE', badges.get(provider, provider[:1].upper()))
    if not badge or len(badge) > 8 or any(ord(c) < 32 or ord(c) == 127 for c in badge):
        raise Error('badge must contain 1–8 printable characters')
    return badge


def read_record(path, session):
    if not path.exists():
        return {'session': session}, None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Error('cannot read surface state: '+str(e)) from e
    if not isinstance(value, dict):
        raise Error('surface state must be a JSON object')
    if 'data' in value and ('revision' in value or 'schema_version' in value):
        if value.get('schema_version') != 1 or not isinstance(value.get('revision'), str) or not isinstance(value.get('data'), dict):
            raise Error('unsupported surface state envelope')
        return value['data'], value['revision']
    return value, None


def publisher_command(provider, session, environ=None):
    environ = os.environ if environ is None else environ
    configured = environ.get('HARNESS_UI_PUBLISH_COMMAND', environ.get('CODEX_UI_PUBLISH_COMMAND'))
    if configured is None:
        return None
    try:
        command = json.loads(configured)
    except ValueError as e:
        raise Error('HARNESS_UI_PUBLISH_COMMAND (or CODEX_UI_PUBLISH_COMMAND) must be a JSON argv array') from e
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise Error('HARNESS_UI_PUBLISH_COMMAND (or CODEX_UI_PUBLISH_COMMAND) must be a nonempty JSON argv array')
    return [arg.replace('{provider}', provider).replace('{session}', session) for arg in command]


def write_record(path, record, revision, command, environment=None):
    if revision is not None and command is None:
        raise Error('provenance envelope requires HARNESS_UI_PUBLISH_COMMAND (or CODEX_UI_PUBLISH_COMMAND); no state changed')
    if command:
        env = dict(os.environ, **(environment or {}))
        env['CODEX_UI_EXPECT_REVISION'] = env['HARNESS_UI_EXPECT_REVISION'] = revision or ''
        depth = int(env.get('HARNESS_UI_PUBLISH_DEPTH', '0'))
        if depth >= 2:
            raise Error('recursive publisher configuration refused')
        env['HARNESS_UI_PUBLISH_DEPTH'] = str(depth+1)
        try:
            result = subprocess.run(command, input=json.dumps(record), capture_output=True, text=True,
                                    timeout=15, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Error('surface publisher failed: '+str(e)) from e
        if result.returncode:
            raise Error(f'surface publisher exited {result.returncode}; state may have changed, reread/retry: {result.stderr[-1000:]}')
    else:
        atomic_json(path, record)


def entries(record):
    value = record.get('surfaced', [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise Error('record surfaced must be an array of objects')
    ids = [item.get('id') for item in value]
    if any(not isinstance(identity, str) for identity in ids) or len(ids) != len(set(ids)):
        raise Error('surfaced entries require unique string ids')
    return value


def matching(record, wanted):
    found = next((entry for entry in entries(record) if entry['id'] == wanted['id']), None)
    if found is not None and any(found.get(k) != wanted[k] for k in ('kind', 'text', 'source_anchor', 'surfaced_by')):
        raise Error('surface id already belongs to different content')
    return found


def surface(kind, session, text=None, source_anchor=None, identity=None, provider='codex', root=None, manager=None):
    if kind not in ('ask', 'note', 'decision', 'resolve'):
        raise Error('surface kind must be ask, note, decision or resolve')
    if not SAFE.fullmatch(session) or not SAFE.fullmatch(provider):
        raise Error('invalid provider/session identifier')
    if kind == 'resolve':
        if not identity:
            raise Error('resolve requires --id')
    elif not isinstance(text, str) or not text.strip() or not isinstance(source_anchor, str) or not source_anchor.strip():
        raise Error('surface requires nonempty --text and --source-anchor')
    if identity is None:
        identity = hashlib.sha256(json.dumps([kind, text, source_anchor], ensure_ascii=False).encode()).hexdigest()[:24]
    if not SAFE.fullmatch(identity):
        raise Error('invalid content id')
    settings = resolve_state(provider, manager)
    environment = dict(os.environ, **settings['environment'])
    root = Path(root) if root is not None else Path(settings['state_root'])
    path = session_path(root, provider, session)
    command = publisher_command(provider, session, environment)
    with session_lock(path):
        record, revision = read_record(path, session)
        existing = next((entry for entry in entries(record) if entry['id'] == identity), None)
        if kind == 'resolve':
            if existing is None:
                raise Error('cannot resolve unknown surface id')
            if any(key not in existing for key in ('kind', 'text', 'source_anchor', 'surfaced_by')):
                raise Error('cannot resolve malformed surface entry')
            wanted = dict(existing, status='resolved')
        else:
            wanted = {'id': identity, 'kind': kind, 'text': text, 'source_anchor': source_anchor,
                      'surfaced_by': provider, 'badge': badge_for(provider, environment), 'status': 'open'}
            existing = matching(record, wanted)
            if existing and existing.get('status') == 'resolved':
                raise Error('surface id is resolved; use a new id for new content')
        if existing and existing.get('verified') is True and existing.get('status', 'open') == wanted['status']:
            return {'id': identity, 'result': 'already_present', 'verified': True, 'status': wanted['status']}
        if revision is not None and command is None:
            raise Error('provenance envelope requires HARNESS_UI_PUBLISH_COMMAND (or CODEX_UI_PUBLISH_COMMAND); no state changed')
        if existing is None or kind == 'resolve':
            first = deepcopy(record)
            updated = [entry for entry in entries(first) if entry['id'] != identity]
            first['surfaced'] = updated+[dict(wanted, verified=False)]
            write_record(path, first, revision, command, environment)
        # Read once before authorizing the badge, and merge against that latest
        # record so another publisher's unrelated fields survive confirmation.
        stored, revision = read_record(path, session)
        found = matching(stored, wanted)
        if found is None or found.get('status', 'open') != wanted['status']:
            raise Error('surface publisher did not persist requested content')
        confirmed = deepcopy(stored)
        confirmed['surfaced'] = [dict(entry, verified=True) if entry['id'] == identity else entry for entry in entries(confirmed)]
        write_record(path, confirmed, revision, command, environment)
        stored, _ = read_record(path, session)
        found = matching(stored, wanted)
        if not found or found.get('verified') is not True or found.get('status', 'open') != wanted['status']:
            raise Error('surface confirmation read-back failed')
        return {'id': identity, 'result': 'published', 'verified': True, 'status': wanted['status']}


def publish_session(provider, session, input_file, retain_fields=(), consume=False, root=None, manager=None, merge=False):
    """Shared raw/envelope writer used by external harness adapters.

    By default the input replaces the record, keeping only `retain_fields` from it.
    With `merge`, the input's top-level fields are laid over the current record, read
    under the same session lock, so a writer that owns a few fields (usage, terminal,
    presence) never drops another writer's; the configured publisher and read-back apply
    as for any write.
    """
    settings = resolve_state(provider, manager)
    environment = dict(os.environ, **settings['environment'])
    root = Path(root) if root is not None else Path(settings['state_root'])
    path = session_path(root, provider, session)
    source = Path(input_file)
    if source.resolve() == path.resolve():
        raise Error('input file must differ from the published state path')
    try:
        original = source.read_bytes()
        record = json.loads(original)
    except (OSError, ValueError) as e:
        raise Error('cannot read publish input: '+str(e)) from e
    if not isinstance(record, dict):
        raise Error('publish input must be a JSON object')
    if 'session' in record and record['session'] != session:
        raise Error('publish input session does not match requested session')
    if any(not isinstance(field, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field) for field in retain_fields):
        raise Error('retain-field accepts top-level field names only')
    command = publisher_command(provider, session, environment)
    with session_lock(path):
        previous, revision = read_record(path, session)
        expected = os.environ.get('HARNESS_UI_EXPECT_REVISION', os.environ.get('CODEX_UI_EXPECT_REVISION'))
        if expected is not None and expected != (revision or ''):
            raise Error('state revision changed; reread/retry')
        if merge:
            record = dict(deepcopy(previous), **record)
        for field in retain_fields:
            if field not in record and field in previous:
                record[field] = deepcopy(previous[field])
        write_record(path, record, revision, command, environment)
        saved, _ = read_record(path, session)
        if saved != record:
            raise Error('session publisher read-back failed')
    if consume:
        if source.read_bytes() != original:
            raise Error('publication succeeded but input changed; refusing to consume it')
        source.unlink()
    return {'provider': provider, 'session': session, 'published': True, 'verified': True}
