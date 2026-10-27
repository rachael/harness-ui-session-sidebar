"""Which harness sessions are running, and in which terminal tab.

A session announces itself from its harness's own hook (`sessions announce`): the
harness process, its start time and controlling tty, and the terminal handle its
environment names. That goes into the session's shared record as its `terminal` field
(`sessions/PROVIDER/SESSION.json`; contracts/session-sidebar.md), merged under the
record's lock so every other field stays as it was. Listing reads those records.
Nothing reads transcripts, runs a model or keeps a process of its own; a session's
state is only what the process table says (running, ended, unknown). Whether someone
is working in it, or it waits on the person, is the record's `activity`, which only the
harness's own events write (contracts/overview.md); this module only reads it.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from .core import Error
from .surface import SAFE, badge_for, publish_session, read_record, resolve_state, session_path
from . import terminals

SCHEMA = 1
FIELD = 'terminal'
RUNNING, ENDED, UNKNOWN = 'running', 'ended', 'unknown'
WORKING, IDLE, AWAITING = 'working', 'idle', 'awaiting_input'
# Harness groups in the order they are listed; any other harness follows, by name.
HARNESSES = ('claude', 'codex')
# Wrappers between a harness and the command its hook runs; the harness is the first
# ancestor that is not one of these.
WRAPPERS = {'sh', 'bash', 'zsh', 'dash', 'fish', 'env', 'uv', 'uvx', 'python', 'python3', 'harness-ui', 'codex-ui'}
MAX_SESSIONS = 200
MAX_BYTES = 1_000_000


def process_info(pid):
    """{ppid, tty, start, command} for a live pid from ps, or None when it is gone."""
    try:
        p = subprocess.run(['ps', '-o', 'ppid=', '-o', 'tty=', '-o', 'lstart=', '-o', 'comm=', '-p', str(int(pid))],
                           capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    fields = p.stdout.split()
    if p.returncode or len(fields) < 8:
        return None
    tty = fields[1]
    tty = None if tty in ('??', '?', '-') else (tty if tty.startswith('/dev/') else '/dev/'+tty)
    return {'ppid': int(fields[0]), 'tty': tty, 'start': ' '.join(fields[2:7]), 'command': ' '.join(fields[7:])}


def harness_pid(start=None, ps=process_info):
    """The nearest ancestor of this process that is not a shell or launcher wrapper."""
    pid = start or os.getppid()
    for _ in range(8):
        info = ps(pid)
        if info is None or os.path.basename(info['command']).lstrip('-') not in WRAPPERS or info['ppid'] <= 1:
            return pid
        pid = info['ppid']
    return pid


def settings(provider, root=None, manager=None):
    """(state root, environment, publisher command), as publish-session resolves them."""
    resolved = resolve_state(provider, manager)
    return Path(root if root is not None else resolved['state_root']), dict(os.environ, **resolved['environment'])


def merge_field(provider, session, value, root=None, manager=None):
    """Set the record's `terminal` field (None clears it), keeping every other field.

    Through the shared writer's merge (`publish_session(merge=True)`): under the record's
    own lock, through the configured publisher when there is one, read back before it
    counts. A merge cannot remove a key, so a forgotten session keeps `"terminal": null`.
    """
    for attempt in range(20):
        staged = tempfile.NamedTemporaryFile('w', suffix='.json', prefix='harness-ui-terminal-', delete=False)
        try:
            with staged:
                json.dump({FIELD: value}, staged)
            publish_session(provider, session, staged.name, consume=True, root=root, manager=manager, merge=True)
            return
        except Error as e:
            if 'another surface update is running' not in str(e) or attempt == 19:
                raise
            time.sleep(0.05)
        finally:
            Path(staged.name).unlink(missing_ok=True)


def liveness(entry, ps=process_info):
    pid, start = entry.get('pid'), entry.get('start')
    if not isinstance(pid, int) or not isinstance(start, str):
        return UNKNOWN, None
    info = ps(pid)
    # A pid reused by another process has another start time.
    return (RUNNING, info) if info is not None and info['start'] == start else (ENDED, None)


def announce(provider, session, pid=None, root=None, environ=None, ps=process_info, now=None, manager=None):
    """Record this session's harness process and terminal handle in its shared record."""
    environ = os.environ if environ is None else environ
    pid = pid or harness_pid(ps=ps)
    info = ps(pid)
    if info is None:
        raise Error('harness process '+str(pid)+' is not running')
    value = {'schema': SCHEMA, 'pid': pid, 'start': info['start'], 'tty': info['tty'],
             'handle': terminals.detect(environ, info['tty']), 'cwd': os.getcwd(),
             'announced_at': time.time() if now is None else now}
    merge_field(provider, session, value, root, manager)
    return {'provider': provider, 'session': session, 'announced': True, 'terminal': (value['handle'] or {}).get('kind')}


def forget(provider, session, root=None, manager=None):
    merge_field(provider, session, None, root, manager)
    return {'provider': provider, 'session': session, 'forgotten': True}


def records(root):
    """(provider, session, record, mtime) for each record with a terminal field."""
    for path in sorted(Path(root).glob('sessions/*/*.json'))[:MAX_SESSIONS*5]:
        provider, session = path.parent.name, path.stem
        if not SAFE.fullmatch(provider) or not SAFE.fullmatch(session):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            record, _ = read_record(path, session)
            mtime = path.stat().st_mtime
        except (OSError, Error):
            continue
        entry = record.get(FIELD)
        if isinstance(entry, dict) and entry.get('schema') == SCHEMA:
            yield provider, session, record, mtime


def text(record, key):
    value = record.get(key)
    return value if isinstance(value, str) else ''


def started_at(start):
    """A process table start time (`ps -o lstart`) as seconds since the epoch, or None."""
    try:
        return time.mktime(time.strptime(' '.join(start.split()), '%a %b %d %H:%M:%S %Y'))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def activity(record):
    """{status, needs_input, updated_at}: what the harness's own events last said.

    `unknown` when no event was recorded, when the field is malformed, or when it is older
    than the harness process now running (the last run of a resumed session). A running
    process says nothing about this.
    """
    value, started = record.get('activity'), started_at(record[FIELD].get('start'))
    at = value.get('updatedAt') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('status') not in (WORKING, IDLE, AWAITING, UNKNOWN)
            or isinstance(at, bool) or not isinstance(at, (int, float)) or started is None or at/1000 < started):
        return {'status': UNKNOWN, 'needs_input': False, 'updated_at': None}
    return {'status': value['status'], 'needs_input': value.get('needs_input') is True, 'updated_at': at/1000}


def describe(provider, session, record, mtime, state, badge=''):
    entry = record[FIELD]
    handle = entry.get('handle') if isinstance(entry.get('handle'), dict) else {}
    return {'provider': provider, 'badge': badge, 'session': session, 'state': state, 'title': text(record, 'title'),
            'cwd': text(record, 'cwd') or text(entry, 'cwd'), 'branch': text(record, 'branch'), 'model': text(record, 'model'),
            'updated_at': mtime, 'activity': activity(record),
            'terminal': {'kind': handle.get('kind', 'none'),
                         'focusable': state == RUNNING and handle.get('kind') in terminals.ADAPTERS}}


def badge(provider, environ=None):
    """The harness's badge, as the info pane marks it (HARNESS_UI_BADGES overrides)."""
    try:
        return badge_for(provider, environ)
    except Error:
        return badge_for(provider, {})


def group(provider):
    return (HARNESSES.index(provider), '') if provider in HARNESSES else (len(HARNESSES), provider)


def list_sessions(root=None, provider='codex', include_ended=False, hidden=(), ps=process_info, manager=None):
    """Announced sessions grouped by harness, newest first in each; ended ones only when asked."""
    root, environment = settings(provider, root, manager)
    rows = []
    for provider_, session, record, mtime in records(root):
        if provider_ in hidden:
            continue
        state, _ = liveness(record[FIELD], ps)
        if state != ENDED or include_ended:
            rows.append(describe(provider_, session, record, mtime, state, badge(provider_, environment)))
    rows.sort(key=lambda row: (group(row['provider']), -row['updated_at'], row['session']))
    return {'schema': SCHEMA, 'sessions': rows[:MAX_SESSIONS]}


def entry_for(root, provider, session):
    path = session_path(root, provider, session)
    try:
        record, _ = read_record(path, session) if path.exists() and path.stat().st_size <= MAX_BYTES else ({}, None)
    except Error:
        record = {}
    entry = record.get(FIELD)
    return entry if isinstance(entry, dict) and entry.get('schema') == SCHEMA else None


def caller_handle(environ=None, ps=process_info):
    """The terminal the request comes from: this process's own environment and tty."""
    info = ps(os.getpid())
    return terminals.detect(os.environ if environ is None else environ, info['tty'] if info else None)


def owns(entry, info):
    """Whether a running harness still holds the tab its tty handle names."""
    adapter = terminals.ADAPTERS.get((entry.get('handle') or {}).get('kind'))
    return adapter is None or not adapter.needs_owner_tty or (bool(info['tty']) and info['tty'] == entry['handle'].get('tty'))


def focus_session(provider, session, caller=None, switch_tabs=True, switch_panes=True, root=None,
                  ps=process_info, run=terminals.run, manager=None):
    """Switch to the tab this session runs in, inside the caller's window, or say why not."""
    root, _ = settings(provider, root, manager)
    entry = entry_for(root, provider, session)
    if entry is None:
        return terminals.result(terminals.MISSING, 'this session has not announced a terminal')
    state, info = liveness(entry, ps)
    if state != RUNNING:
        return terminals.result(ENDED if state == ENDED else terminals.UNSUPPORTED,
                                'the session has ended' if state == ENDED else 'no harness process was recorded')
    if not owns(entry, info):
        return terminals.result(terminals.MISSING, 'the session no longer holds the tab it was announced from')
    caller = caller_handle(ps=ps) if caller is None else caller
    return terminals.focus(entry.get('handle'), caller, switch_tabs, switch_panes, run)


def add_cli(subparsers):
    """The `sessions` command, for the main CLI to mount: add_cli(sub), then handle(args)."""
    sessions = subparsers.add_parser('sessions', help='List running harness sessions and switch to their terminal tabs')
    commands = sessions.add_subparsers(dest='sessions_command', required=True)
    announce_ = commands.add_parser('announce', help="Record this session's harness process and terminal (from a harness hook)")
    announce_.add_argument('provider'); announce_.add_argument('session')
    announce_.add_argument('--pid', type=int, help='The harness process (default: the nearest non-wrapper ancestor)')
    forget_ = commands.add_parser('forget'); forget_.add_argument('provider'); forget_.add_argument('session')
    list_ = commands.add_parser('list'); list_.add_argument('--all', action='store_true', help='Include ended sessions')
    list_.add_argument('--hide', action='append', default=[], metavar='HARNESS', help="Leave out one harness's sessions (repeatable)")
    focus_ = commands.add_parser('focus', help="Switch to a session's tab, inside this window only")
    focus_.add_argument('provider'); focus_.add_argument('session')
    focus_.add_argument('--switch-tabs', action=argparse.BooleanOptionalAction, default=True)
    focus_.add_argument('--switch-panes', action=argparse.BooleanOptionalAction, default=True)
    layout_ = commands.add_parser('layout', help='Run a harness in tmux with the session sidebar in a pane on its left')
    layout_.add_argument('--width', type=int, default=32)
    layout_.add_argument('--switch-tabs', action=argparse.BooleanOptionalAction, help='Default: HARNESS_UI_SIDEBAR_SWITCH_TABS, else on')
    layout_.add_argument('--switch-panes', action=argparse.BooleanOptionalAction, help='Default: HARNESS_UI_SIDEBAR_SWITCH_PANES, else on')
    layout_.add_argument('--hide', action='append', metavar='HARNESS', help='Leave out a harness (default: HARNESS_UI_SIDEBAR_SHOW_*)')
    layout_.add_argument('harness', nargs=argparse.REMAINDER, help='-- then the harness command, e.g. -- claude')
    sidebar_ = commands.add_parser('sidebar', help='The sidebar itself, as the layout runs it in its left pane')
    sidebar_.add_argument('rest', nargs=argparse.REMAINDER)
    return sessions


def handle(args, manager=None):
    """Prints one JSON object; returns the exit code (focus: 0 when focused or already there)."""
    command = args.sessions_command
    if command in ('layout', 'sidebar'):
        from . import sidebar
        if command == 'sidebar':
            return sidebar.main(args.rest, manager=manager)
        # The installer's options reach the sidebar pane as arguments: a tmux pane does not
        # inherit this process's environment.
        _, environment = settings('codex', manager=manager)
        if manager is not None:
            environment['HARNESS_UI_HOME'] = str(manager.root)
            environment.update(manager.module_options_environment(manager.selection(None)))
        options = sidebar.options_argv(sidebar.choices(environment, args.switch_tabs, args.switch_panes, args.hide))
        harness = args.harness[1:] if args.harness[:1] == ['--'] else args.harness
        sidebar.layout(harness, width=max(16, min(60, args.width)), options=options, environ=environment)
        return 0
    if command == 'announce':
        out = announce(args.provider, args.session, pid=args.pid, manager=manager)
    elif command == 'forget':
        out = forget(args.provider, args.session, manager=manager)
    elif command == 'list':
        out = list_sessions(include_ended=args.all, hidden=set(args.hide), manager=manager)
    else:
        out = focus_session(args.provider, args.session, switch_tabs=args.switch_tabs, switch_panes=args.switch_panes, manager=manager)
    print(json.dumps(out))
    return 0 if command != 'focus' or out['status'] in (terminals.FOCUSED, terminals.ALREADY) else 3


def main(argv=None):
    parser = argparse.ArgumentParser(prog='harness-ui')
    add_cli(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(argv)
    try:
        return handle(args)
    except Error as e:
        print(json.dumps({'error': str(e)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
