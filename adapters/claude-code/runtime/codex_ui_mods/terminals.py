"""Terminal adapters: name the tab a session runs in, and switch to exactly that tab.

A handle is an opaque, stable identifier the terminal itself reports (iTerm2's session
guid, Terminal.app's tab tty, a tmux pane id), captured from the harness's own
environment when the session announces itself.

Switching only ever happens inside the window the request comes from (the caller's),
and only to a tab whose pane sits where the caller's does and is the same size, all
resolved when the switch runs. A target in another window, a pane of another size or
position, a handle no tab holds any more: each is refused and reported, and nothing
else is focused instead. Never a title, never the nearest tab. Handles reach the
terminal as arguments (`on run argv`, an argv), never as script text.
"""
import re
import subprocess

FOCUSED, ALREADY, MISSING, OTHER_WINDOW, GEOMETRY, DISABLED = (
    'focused', 'already', 'missing', 'other-window', 'geometry', 'disabled')
CALLER_MISSING, UNAVAILABLE, UNSUPPORTED, DENIED, FAILED = (
    'caller-missing', 'unavailable', 'unsupported', 'denied', 'failed')
# What a script may answer, as `status` or `status:detail`.
ANSWERS = {FOCUSED, ALREADY, MISSING, OTHER_WINDOW, GEOMETRY, DISABLED, CALLER_MISSING, UNAVAILABLE}
GUID = re.compile(r'^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$')
TTY = re.compile(r'^/dev/ttys?[0-9]{1,4}$')
PANE = re.compile(r'^%[0-9]{1,6}$')
SOCKET = re.compile(r'^/[^\x00-\x1f,]{1,1000}$')


def run(argv, stdin=None, timeout=10):
    """Run a command; (exit code, stdout, stderr). Tests replace this."""
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, '', argv[0]+' not found'
    except subprocess.TimeoutExpired:
        return 124, '', argv[0]+' timed out'
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def result(status, detail=''):
    return {'status': status, 'detail': detail}


def flag(value):
    return '1' if value else '0'


class AppleScriptAdapter:
    """A terminal driven through its scripting dictionary; the script answers one line."""
    kind = ''
    app = ''
    script = ''
    # Whether the handle proves ownership only while the announcing process still holds
    # its controlling tty (a tty is reused once its tab closes).
    needs_owner_tty = False

    def detect(self, environ, tty):
        raise NotImplementedError

    def valid(self, handle):
        raise NotImplementedError

    def argument(self, handle):
        raise NotImplementedError

    def focus(self, target, caller, switch_tabs=True, switch_panes=True, run=run):
        if not self.valid(target):
            return result(UNSUPPORTED, 'malformed '+self.kind+' handle')
        if not isinstance(caller, dict) or caller.get('kind') != self.kind or not self.valid(caller):
            return result(OTHER_WINDOW, 'the request does not come from a '+self.app+' window')
        code, out, err = run(['osascript', '-', self.argument(target), self.argument(caller),
                              flag(switch_tabs), flag(switch_panes)], self.script)
        status, _, detail = out.partition(':')
        if code == 0 and status in ANSWERS:
            return result(status, detail or {MISSING: 'no '+self.app+' tab holds this session any more',
                                             OTHER_WINDOW: 'the session is in another '+self.app+' window',
                                             CALLER_MISSING: 'the requesting tab was not found',
                                             UNAVAILABLE: self.app+' is not running'}.get(status, ''))
        # -1743: the person has not allowed automation of this app (System Settings,
        # Privacy & Security, Automation).
        if '-1743' in err:
            return result(DENIED, 'automation of '+self.app+' is not allowed for this process')
        return result(FAILED, (err or out or 'exit '+str(code))[-300:])


class ITerm2(AppleScriptAdapter):
    kind = 'iterm2'
    app = 'iTerm2'
    # iTerm2's sdef: a session's `id` is its guid; tab and session respond to `select`;
    # a session reports `columns` and `rows` but not its position, so a tab split into
    # panes cannot be shown to hold the target where the caller's pane is, and is refused.
    script = '''on run argv
  set wanted to item 1 of argv
  set caller to item 2 of argv
  set allowTabs to (item 3 of argv) is "1"
  set allowPanes to (item 4 of argv) is "1"
  if application "iTerm2" is not running then return "unavailable"
  tell application "iTerm2"
    set homeId to missing value
    repeat with w in windows
      repeat with ti from 1 to count of tabs of w
        repeat with s in sessions of tab ti of w
          if (id of s) is caller then
            set homeId to id of w
            set homeTab to ti
            set homeCols to columns of s
            set homeRows to rows of s
          end if
        end repeat
      end repeat
    end repeat
    if homeId is missing value then return "caller-missing"
    if wanted is caller then return "already"
    set home to (first window whose id is homeId)
    repeat with ti from 1 to count of tabs of home
      set t to tab ti of home
      repeat with s in sessions of t
        if (id of s) is wanted then
          if ti is homeTab then return "geometry:another pane of this tab"
          if not allowTabs then return "disabled:switching tabs is off"
          if (count of sessions of t) > 1 then
            if not allowPanes then return "disabled:switching panes is off"
            return "geometry:iTerm2 scripting does not report pane positions"
          end if
          if (columns of s) is not homeCols or (rows of s) is not homeRows then return "geometry:the tab is another size"
          select t
          select s
          return "focused"
        end if
      end repeat
    end repeat
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is wanted then return "other-window"
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
'''

    def detect(self, environ, tty):
        if environ.get('TERM_PROGRAM') != 'iTerm.app':
            return None
        # ITERM_SESSION_ID is `w0t1p0:GUID`; the prefix is a position and changes as tabs
        # move, the guid is the session's `id` and does not.
        guid = environ.get('ITERM_SESSION_ID', '').rpartition(':')[2]
        return {'kind': self.kind, 'id': guid} if GUID.fullmatch(guid) else None

    def valid(self, handle):
        return isinstance(handle, dict) and isinstance(handle.get('id'), str) and bool(GUID.fullmatch(handle['id']))

    def argument(self, handle):
        return handle['id']


class AppleTerminal(AppleScriptAdapter):
    kind = 'apple-terminal'
    app = 'Terminal'
    # Terminal.app names no stable tab id; a tab's tty is unique while a process holds it,
    # so the registry asks for a switch only while the announcing process still does.
    # Its tabs have no panes.
    needs_owner_tty = True
    script = '''on run argv
  set wanted to item 1 of argv
  set caller to item 2 of argv
  set allowTabs to (item 3 of argv) is "1"
  if application "Terminal" is not running then return "unavailable"
  tell application "Terminal"
    set homeId to missing value
    repeat with w in windows
      repeat with t in tabs of w
        if (tty of t) is caller then
          set homeId to id of w
          set homeRows to number of rows of t
          set homeCols to number of columns of t
        end if
      end repeat
    end repeat
    if homeId is missing value then return "caller-missing"
    if wanted is caller then return "already"
    set home to (first window whose id is homeId)
    repeat with t in tabs of home
      if (tty of t) is wanted then
        if not allowTabs then return "disabled:switching tabs is off"
        if (number of rows of t) is not homeRows or (number of columns of t) is not homeCols then return "geometry:the tab is another size"
        set selected of t to true
        return "focused"
      end if
    end repeat
    repeat with w in windows
      repeat with t in tabs of w
        if (tty of t) is wanted then return "other-window"
      end repeat
    end repeat
  end tell
  return "missing"
end run
'''

    def detect(self, environ, tty):
        if environ.get('TERM_PROGRAM') != 'Apple_Terminal' or not tty or not TTY.fullmatch(tty):
            return None
        return {'kind': self.kind, 'tty': tty}

    def valid(self, handle):
        return isinstance(handle, dict) and isinstance(handle.get('tty'), str) and bool(TTY.fullmatch(handle['tty']))

    def argument(self, handle):
        return handle['tty']


class Tmux:
    """tmux: a tmux session is the window, its windows the tabs, panes the panes.

    tmux reports every pane's position and size, so a switch to another window is made
    only when the target pane sits exactly where the caller's does.
    """
    kind = 'tmux'
    app = 'tmux'
    needs_owner_tty = False
    FORMAT = '#{session_id} #{window_id} #{pane_id} #{window_panes} #{pane_left} #{pane_top} #{pane_width} #{pane_height}'

    def detect(self, environ, tty):
        socket = environ.get('TMUX', '').split(',')[0]
        pane = environ.get('TMUX_PANE', '')
        return {'kind': self.kind, 'socket': socket, 'pane': pane} if SOCKET.fullmatch(socket) and PANE.fullmatch(pane) else None

    def valid(self, handle):
        return (isinstance(handle, dict) and isinstance(handle.get('socket'), str) and isinstance(handle.get('pane'), str)
                and bool(SOCKET.fullmatch(handle['socket'])) and bool(PANE.fullmatch(handle['pane'])))

    def where(self, handle, run):
        code, out, _ = run(['tmux', '-S', handle['socket'], 'display-message', '-p', '-t', handle['pane'], self.FORMAT])
        fields = out.split()
        if code or len(fields) != 8 or fields[2] != handle['pane']:
            return None
        return {'session': fields[0], 'window': fields[1], 'panes': int(fields[3]), 'box': tuple(map(int, fields[4:]))}

    def focus(self, target, caller, switch_tabs=True, switch_panes=True, run=run):
        if not self.valid(target):
            return result(UNSUPPORTED, 'malformed tmux handle')
        if not self.valid(caller) or caller['socket'] != target['socket']:
            return result(OTHER_WINDOW, 'the request does not come from this tmux server')
        home = self.where(caller, run)
        if home is None:
            return result(CALLER_MISSING, 'the requesting pane was not found')
        if target['pane'] == caller['pane']:
            return result(ALREADY)
        there = self.where(target, run)
        if there is None:
            return result(MISSING, 'no tmux pane holds this session any more')
        if there['session'] != home['session']:
            return result(OTHER_WINDOW, 'the session is in another tmux session')
        if there['window'] == home['window']:
            return result(GEOMETRY, 'another pane of this window')
        if not switch_tabs:
            return result(DISABLED, 'switching tabs is off')
        if there['panes'] > 1 and not switch_panes:
            return result(DISABLED, 'switching panes is off')
        if there['box'] != home['box']:
            return result(GEOMETRY, 'the pane is in another place or another size')
        for argv in (['select-window', '-t', there['window']], ['select-pane', '-t', target['pane']]):
            code, out, err = run(['tmux', '-S', target['socket'], *argv])
            if code:
                return result(FAILED, (err or out)[-300:])
        return result(FOCUSED)


ADAPTERS = {}


def register_adapter(adapter):
    """Add or replace a terminal adapter by its kind; detection tries them in order."""
    ADAPTERS[adapter.kind] = adapter
    return adapter


# A multiplexer first: inside one, the outer terminal's tab is not the session's own.
register_adapter(Tmux())
register_adapter(ITerm2())
register_adapter(AppleTerminal())


def detect(environ, tty=None):
    """The terminal handle for a process with this environment and controlling tty."""
    for adapter in ADAPTERS.values():
        handle = adapter.detect(environ, tty)
        if handle:
            return handle
    if environ.get('TMUX') or environ.get('STY') or environ.get('ZELLIJ'):
        return {'kind': 'multiplexer'}
    program = environ.get('TERM_PROGRAM', '')
    return {'kind': 'unknown', 'program': program[:64]} if program else None


def focus(target, caller, switch_tabs=True, switch_panes=True, run=run):
    if not isinstance(target, dict):
        return result(UNSUPPORTED, 'no terminal handle was recorded for this session')
    adapter = ADAPTERS.get(target.get('kind'))
    if adapter is None:
        reason = {'multiplexer': 'runs inside a terminal multiplexer this has no adapter for'}.get(
            target.get('kind'), 'no adapter for terminal '+repr(target.get('program') or target.get('kind')))
        return result(UNSUPPORTED, reason)
    return adapter.focus(target, caller, switch_tabs, switch_panes, run)
