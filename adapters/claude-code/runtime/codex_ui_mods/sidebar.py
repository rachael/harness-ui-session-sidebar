"""A session sidebar on the left of a harness, as a tmux pane (contracts/session-sidebar.md).

`sessions layout -- CMD` runs the harness in a tmux pane and opens a narrow pane on its
left running `sessions sidebar`. Inside tmux already, it splits the current pane; else
it starts a tmux session on the person's own server and configuration and attaches.

Rows are grouped by harness, each under its badge and name, and carry one character for
what the harness's own events last said (working, idle, waiting on the person, unknown);
the words are in the row's details. The installer's options arrive as HARNESS_UI_SIDEBAR_*
environment variables and are passed to the sidebar pane as arguments.

The sidebar switches to a session's tmux window with the harness pane beside it (the
owner) as the origin: the target's pane must be where the owner's is and the same size.
The owner is named explicitly and checked before every switch; the layout itself is
never changed by a switch. Everything slow (the process table, tmux) runs on a worker
thread; drawing only reads the last snapshot.
"""
import argparse
import curses
import os
from pathlib import Path
import shlex
import sys
import threading
import time

from . import sessions, terminals

WIDTH = 32
REFRESH_SECONDS = 5
PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])
SHOW = {'claude': 'HARNESS_UI_SIDEBAR_SHOW_CLAUDE', 'codex': 'HARNESS_UI_SIDEBAR_SHOW_CODEX'}
NAMES = {'claude': 'Claude Code', 'codex': 'Codex'}
GLYPHS = {sessions.WORKING: '●', sessions.IDLE: '○', sessions.AWAITING: '?', sessions.UNKNOWN: '·'}
WORDS = {sessions.WORKING: 'working', sessions.IDLE: 'idle', sessions.AWAITING: 'waiting on you',
         sessions.UNKNOWN: 'activity unknown'}
# Each harness's own accent for its heading, where the terminal has colors: Claude's
# orange, Codex's cyan.
ACCENTS = {'claude': (173, curses.COLOR_YELLOW), 'codex': (curses.COLOR_CYAN, curses.COLOR_CYAN)}


def setting(environ, name):
    """An installer option from the environment: on unless it says 0, false, no or off."""
    return environ.get(name, '').strip().lower() not in ('0', 'false', 'no', 'off')


def choices(environ, switch_tabs=None, switch_panes=None, hide=None):
    """The sidebar's options: those given, else the installer's environment, else on."""
    return {'switch_tabs': setting(environ, 'HARNESS_UI_SIDEBAR_SWITCH_TABS') if switch_tabs is None else switch_tabs,
            'switch_panes': setting(environ, 'HARNESS_UI_SIDEBAR_SWITCH_PANES') if switch_panes is None else switch_panes,
            'hide': sorted(set(hide or ()) | {harness for harness, name in SHOW.items() if not setting(environ, name)})}


def options_argv(chosen):
    return (['--switch-tabs' if chosen['switch_tabs'] else '--no-switch-tabs',
             '--switch-panes' if chosen['switch_panes'] else '--no-switch-panes']
            + [arg for harness in chosen['hide'] for arg in ('--hide', harness)])


def tmux_argv(socket=None):
    return ['tmux', *(['-S', socket] if socket else [])]


def sidebar_command(owner, socket=None, options=(), prefix=()):
    return shlex.join([*prefix, sys.executable, '-m', 'codex_ui_mods.sidebar', '--owner-pane', owner,
                       *(['--socket', socket] if socket else []), *options])


def layout(argv, width=WIDTH, socket=None, environ=None, run=terminals.run, attach=True, options=()):
    """Open the sidebar pane left of the harness pane; returns the owner pane id.

    With `attach`, this process then becomes the harness (inside tmux) or the tmux
    client (outside). The harness command is the person's, run as given.
    """
    environ = os.environ if environ is None else environ
    if not argv:
        raise sessions.Error('layout needs the harness command after --')
    tmux = tmux_argv(socket)
    env = ['-e', 'PYTHONPATH='+os.pathsep.join(filter(None, [PACKAGE_ROOT, environ.get('PYTHONPATH', '')]))]
    # tmux's server may predate this install or use another manager home. Carry
    # the selected shared-state owner into both new panes, never the whole environment.
    prefix = ['env']
    for key in ('HARNESS_UI_HOME', 'CODEX_UI_HOME', 'HARNESS_UI_STATE_DIR', 'CODEX_UI_STATE_DIR',
                'HARNESS_UI_PUBLISH_COMMAND', 'CODEX_UI_PUBLISH_COMMAND', 'HARNESS_UI_BADGE', 'HARNESS_UI_BADGES'):
        if key in environ:
            env.extend(['-e', key+'='+environ[key]])
        else:
            # tmux -e sets values but does not unset inherited server variables.
            # Clear only absent UI keys in each child, leaving the server alone.
            prefix.extend(['-u', key])
    server = environ.get('TMUX', '').split(',')[0]
    inside = bool(server) and bool(terminals.PANE.fullmatch(environ.get('TMUX_PANE', ''))) and socket in (None, server)
    if inside:
        owner, session = environ['TMUX_PANE'], None
    else:
        code, out, err = run([*tmux, 'new-session', '-d', '-P', '-F', '#{session_id} #{pane_id}', *env, shlex.join([*prefix, *argv])])
        if code or len(out.split()) != 2:
            raise sessions.Error('tmux could not start a session: '+(err or out))
        session, owner = out.split()
    for step in ([*tmux, 'split-window', '-hb', '-d', '-l', str(width), '-t', owner, *env, sidebar_command(owner, socket, options, prefix)],
                 [*tmux, 'select-pane', '-t', owner]):
        code, out, err = run(step)
        if code:
            raise sessions.Error('tmux refused the layout: '+(err or out))
    if attach:
        if inside:
            os.execvpe(argv[0], argv, environ)
        os.execvp('tmux', [*tmux, 'attach-session', '-t', session])
    return owner


# Drawing: pure functions of a view, so they are tested without a terminal.

def age(seconds):
    if seconds < 60: return 'now'
    if seconds < 3600: return f'{int(seconds//60)}m'
    if seconds < 86400: return f'{int(seconds//3600)}h'
    return f'{int(seconds//86400)}d'


def clip(text, width):
    return text if len(text) <= width else text[:max(0, width-1)]+'…'


def key_of(row):
    return row['provider']+':'+row['session']


def glyph(activity):
    """Something waiting on the person comes first: an ask can be open while it works."""
    return '?' if activity['needs_input'] else GLYPHS[activity['status']]


def words(activity):
    said = WORDS[activity['status']]
    return said+', waiting on you' if activity['needs_input'] and activity['status'] != sessions.AWAITING else said


def lines(rows, expanded, note, width, now, home=''):
    """[(text, kind, key)]: the heading, the note row (kept, blank when empty), then each
    harness's heading (kind `group`, key the harness) and its rows, as listed."""
    out = [(clip('Sessions  r refresh', width), 'heading', None), (clip(note, width) or ' ', 'note', None)]
    if not rows:
        out.append((clip('No sessions announced', width), 'empty', None))
    tilde = lambda path: '~'+path[len(home):] if home and (path == home or path.startswith(home+'/')) else path
    harness = None
    for row in rows:
        if row['provider'] != harness:
            harness = row['provider']
            out.append((clip((row['badge'] or '•')+' '+NAMES.get(harness, harness), width), 'group', harness))
        key = key_of(row)
        label = row['title'] or tilde(row['cwd']) or row['session']
        when = ' '+age(max(0, now-row['updated_at']))
        head = ('[-]' if key in expanded else '[+]')+glyph(row['activity'])+' '
        room = max(1, width-len(head)-len(when))
        out.append((head+clip(label, room).ljust(room)+when, 'row', key))
        if key in expanded:
            details = [words(row['activity'])+('' if row['state'] == sessions.RUNNING else ' · process '+row['state']),
                       tilde(row['cwd'])+(f" ({row['branch']})" if row['branch'] else ''),
                       row['terminal']['kind']+('' if row['terminal']['focusable'] else ': cannot switch')]
            out += [(clip('    '+line, width), 'detail', key) for line in details if line.strip()]
    return out


def palette():
    """{harness: curses attribute} for the group headings; empty without colors."""
    if not curses.has_colors():
        return {}
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        return {}
    attrs = {}
    for number, (harness, (rich, basic)) in enumerate(ACCENTS.items(), 1):
        try:
            curses.init_pair(number, rich if curses.COLORS >= 256 else basic, -1)
            attrs[harness] = curses.color_pair(number)
        except curses.error:
            pass
    return attrs


def hit(drawn, y, x):
    """What a click at (y, x) does: ('toggle', key) on the [+], ('switch', key) on the row."""
    if 0 <= y < len(drawn) and drawn[y][1] == 'row':
        return ('toggle' if x < 3 else 'switch', drawn[y][2])
    return None


KEYS = {curses.KEY_UP: 'up', ord('k'): 'up', curses.KEY_DOWN: 'down', ord('j'): 'down',
        10: 'switch', 13: 'switch', curses.KEY_ENTER: 'switch', ord(' '): 'toggle', curses.KEY_RIGHT: 'toggle',
        curses.KEY_LEFT: 'collapse', ord('r'): 'refresh', ord('q'): 'quit'}


class Sidebar:
    """The sidebar's state; the worker thread owns every slow call."""
    def __init__(self, owner, socket=None, switch_tabs=True, switch_panes=True, hidden=(), ps=sessions.process_info,
                 run=terminals.run, manager=None):
        self.owner = {'kind': 'tmux', 'socket': socket or os.environ.get('TMUX', '').split(',')[0], 'pane': owner}
        self.switch_tabs, self.switch_panes, self.hidden, self.ps, self.run = switch_tabs, switch_panes, set(hidden), ps, run
        self.rows, self.expanded, self.selected, self.note, self.accents = [], set(), 0, '', {}
        self.lock, self.wake, self.requests, self.done = threading.Lock(), threading.Event(), [], False
        self.manager = manager

    def refresh(self):
        try:
            rows = sessions.list_sessions(hidden=self.hidden, ps=self.ps, manager=self.manager)['sessions']
        except sessions.Error as e:
            rows, self.note = [], 'Sessions unavailable: '+str(e)
        with self.lock:
            self.rows = rows
            self.selected = min(self.selected, max(0, len(rows)-1))

    def switch(self, key):
        provider, _, session = key.partition(':')
        if terminals.Tmux().where(self.owner, self.run) is None:
            out = terminals.result(terminals.CALLER_MISSING, 'the harness pane beside this sidebar is gone')
        else:
            out = sessions.focus_session(provider, session, caller=self.owner, switch_tabs=self.switch_tabs,
                                         switch_panes=self.switch_panes, ps=self.ps, run=self.run, manager=self.manager)
        said = {'focused': '', 'already': 'This is the session beside you.'}.get(out['status'])
        with self.lock:
            self.note = said if said is not None else 'Not switched: '+(out['detail'] or out['status'])

    def work(self):
        while not self.done:
            with self.lock:
                requests, self.requests = self.requests, []
            for request in requests:
                self.switch(request)
            self.refresh()
            self.wake.wait(REFRESH_SECONDS)
            self.wake.clear()

    def ask(self, key=None):
        if key:
            with self.lock:
                self.requests.append(key)
        self.wake.set()

    def act(self, action, key=None):
        """Apply a key's or a click's action; returns False to quit."""
        with self.lock:
            keys = [key_of(row) for row in self.rows]
            current = key or (keys[self.selected] if keys else None)
            if action == 'quit':
                return False
            if action in ('up', 'down') and keys:
                self.selected = max(0, min(len(keys)-1, self.selected+(1 if action == 'down' else -1)))
            elif action == 'toggle' and current:
                self.expanded ^= {current}
            elif action == 'collapse' and current:
                self.expanded.discard(current)
            if key in keys:
                self.selected = keys.index(key)
        if action == 'switch' and current:
            self.ask(current)
        elif action == 'refresh':
            self.ask()
        return True

    def draw(self, screen):
        height, width = screen.getmaxyx()
        with self.lock:
            drawn = lines(self.rows, self.expanded, self.note, max(1, width-1), time.time(), os.environ.get('HOME', ''))
            selected = key_of(self.rows[self.selected]) if self.rows else None
            waiting = {key_of(row) for row in self.rows if glyph(row['activity']) == '?'}
        screen.erase()
        for y, (text, kind, key) in enumerate(drawn[:height]):
            attr = curses.A_BOLD if kind == 'heading' else curses.A_DIM if kind in ('note', 'detail', 'empty') else 0
            if kind == 'group':
                attr = curses.A_BOLD | self.accents.get(key, 0)
            if kind == 'row' and key in waiting:
                attr |= curses.A_BOLD
            if kind == 'row' and key == selected:
                attr |= curses.A_REVERSE
            screen.addnstr(y, 0, text, max(1, width-1), attr)
        screen.refresh()
        return drawn

    def loop(self, screen):
        curses.curs_set(0)
        self.accents = palette()
        curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)
        screen.timeout(250)
        worker = threading.Thread(target=self.work, daemon=True)
        worker.start()
        drawn = []
        while True:
            drawn = self.draw(screen)
            key = screen.getch()
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, _ = curses.getmouse()
                except curses.error:
                    continue
                target = hit(drawn, y, x)
                if target:
                    self.act(*target)
            elif key in KEYS and not self.act(KEYS[key]):
                break
        self.done = True
        self.wake.set()


def main(argv=None, manager=None):
    parser = argparse.ArgumentParser(prog='harness-ui sessions sidebar')
    parser.add_argument('--owner-pane', required=True, help='The tmux pane of the harness this sidebar sits beside')
    parser.add_argument('--socket', help='tmux server socket (default: the one this pane runs in)')
    parser.add_argument('--switch-tabs', action=argparse.BooleanOptionalAction, help='Default: HARNESS_UI_SIDEBAR_SWITCH_TABS, else on')
    parser.add_argument('--switch-panes', action=argparse.BooleanOptionalAction, help='Default: HARNESS_UI_SIDEBAR_SWITCH_PANES, else on')
    parser.add_argument('--hide', action='append', metavar='HARNESS', help='Leave out a harness (and any HARNESS_UI_SIDEBAR_SHOW_* off)')
    args = parser.parse_args(argv)
    if not terminals.PANE.fullmatch(args.owner_pane):
        parser.error('--owner-pane must be a tmux pane id such as %3')
    chosen = choices(os.environ, args.switch_tabs, args.switch_panes, args.hide)
    curses.wrapper(Sidebar(args.owner_pane, args.socket, chosen['switch_tabs'], chosen['switch_panes'], chosen['hide'], manager=manager).loop)
    return 0


if __name__ == '__main__':
    sys.exit(main())
