import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from codex_ui_mods import terminals

GUID = '0F0E0D0C-1111-4222-8333-444455556666'
OTHER = 'AAAABBBB-1111-4222-8333-444455556666'


class Runner:
    """Stands in for osascript/tmux: records each call, answers what the test says."""
    def __init__(self, *answers):
        self.answers = list(answers); self.calls = []

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        return self.answers.pop(0) if self.answers else (0, '', '')


class DetectTests(unittest.TestCase):
    def test_iterm_guid_from_session_id_not_its_position_prefix(self):
        handle = terminals.detect({'TERM_PROGRAM': 'iTerm.app', 'ITERM_SESSION_ID': 'w0t3p1:'+GUID})
        self.assertEqual(handle, {'kind': 'iterm2', 'id': GUID})

    def test_iterm_without_a_well_formed_guid_claims_nothing(self):
        handle = terminals.detect({'TERM_PROGRAM': 'iTerm.app', 'ITERM_SESSION_ID': 'w0t3p1:not-a-guid'})
        self.assertEqual(handle, {'kind': 'unknown', 'program': 'iTerm.app'})

    def test_terminal_app_uses_the_controlling_tty(self):
        self.assertEqual(terminals.detect({'TERM_PROGRAM': 'Apple_Terminal'}, '/dev/ttys004'),
                         {'kind': 'apple-terminal', 'tty': '/dev/ttys004'})
        self.assertEqual(terminals.detect({'TERM_PROGRAM': 'Apple_Terminal'}, None), {'kind': 'unknown', 'program': 'Apple_Terminal'})

    def test_tmux_wins_over_the_outer_terminal(self):
        environ = {'TERM_PROGRAM': 'tmux', 'ITERM_SESSION_ID': 'w0t0p0:'+GUID, 'TMUX': '/tmp/tmux-501/default,123,0', 'TMUX_PANE': '%7'}
        self.assertEqual(terminals.detect(environ), {'kind': 'tmux', 'socket': '/tmp/tmux-501/default', 'pane': '%7'})

    def test_other_multiplexers_and_terminals_are_named_not_guessed(self):
        self.assertEqual(terminals.detect({'STY': '1.fixture'}), {'kind': 'multiplexer'})
        self.assertIsNone(terminals.detect({}))
        self.assertEqual(terminals.focus({'kind': 'unknown', 'program': 'Fictional'}, None)['status'], 'unsupported')


class AppleScriptTests(unittest.TestCase):
    def test_handles_travel_as_arguments_never_as_script_text(self):
        run = Runner((0, 'focused', ''))
        out = terminals.focus({'kind': 'iterm2', 'id': GUID}, {'kind': 'iterm2', 'id': OTHER}, run=run)
        self.assertEqual(out['status'], 'focused')
        (argv, script), = run.calls
        self.assertEqual(argv, ['osascript', '-', GUID, OTHER, '1', '1'])
        self.assertNotIn(GUID, script)
        self.assertIn('first window whose id is homeId', script)

    def test_options_are_passed_and_refusals_reported_with_detail(self):
        run = Runner((0, 'disabled:switching tabs is off', ''))
        out = terminals.focus({'kind': 'iterm2', 'id': GUID}, {'kind': 'iterm2', 'id': OTHER}, switch_tabs=False, run=run)
        self.assertEqual(out, {'status': 'disabled', 'detail': 'switching tabs is off'})
        self.assertEqual(run.calls[0][0][-2:], ['0', '1'])
        for answer, status in (('missing', 'missing'), ('other-window', 'other-window'), ('geometry:the tab is another size', 'geometry'),
                               ('caller-missing', 'caller-missing'), ('unavailable', 'unavailable')):
            got = terminals.focus({'kind': 'iterm2', 'id': GUID}, {'kind': 'iterm2', 'id': OTHER}, run=Runner((0, answer, '')))
            self.assertEqual(got['status'], status)
            self.assertTrue(got['detail'])

    def test_a_request_from_another_terminal_never_reaches_the_app(self):
        run = Runner()
        for caller in (None, {'kind': 'apple-terminal', 'tty': '/dev/ttys001'}, {'kind': 'iterm2', 'id': 'nope'}):
            self.assertEqual(terminals.focus({'kind': 'iterm2', 'id': GUID}, caller, run=run)['status'], 'other-window')
        self.assertEqual(run.calls, [])

    def test_malformed_handle_unknown_answer_and_denied_automation(self):
        run = Runner()
        self.assertEqual(terminals.focus({'kind': 'iterm2', 'id': '"; do shell script "x'}, {'kind': 'iterm2', 'id': OTHER}, run=run)['status'], 'unsupported')
        self.assertEqual(run.calls, [])
        caller = {'kind': 'apple-terminal', 'tty': '/dev/ttys002'}
        target = {'kind': 'apple-terminal', 'tty': '/dev/ttys009'}
        self.assertEqual(terminals.focus(target, caller, run=Runner((0, 'surprise', '')))['status'], 'failed')
        denied = terminals.focus(target, caller, run=Runner((1, '', 'execution error: Not authorized to send Apple events to Terminal. (-1743)')))
        self.assertEqual(denied['status'], 'denied')

    def test_terminal_app_script_selects_a_tab_in_the_callers_window_only(self):
        run = Runner((0, 'focused', ''))
        terminals.focus({'kind': 'apple-terminal', 'tty': '/dev/ttys009'}, {'kind': 'apple-terminal', 'tty': '/dev/ttys002'}, run=run)
        argv, script = run.calls[0]
        self.assertEqual(argv[2:4], ['/dev/ttys009', '/dev/ttys002'])
        self.assertIn('repeat with t in tabs of home', script)
        self.assertNotIn('set index of', script)
        self.assertNotIn('activate', script)


class TmuxMockTests(unittest.TestCase):
    SOCKET = '/tmp/fixture-tmux/default'

    def handle(self, pane):
        return {'kind': 'tmux', 'socket': self.SOCKET, 'pane': pane}

    def test_another_socket_is_another_window(self):
        run = Runner()
        out = terminals.focus(self.handle('%2'), {'kind': 'tmux', 'socket': '/tmp/elsewhere', 'pane': '%1'}, run=run)
        self.assertEqual(out['status'], 'other-window')
        self.assertEqual(run.calls, [])

    def test_geometry_mismatch_selects_nothing(self):
        run = Runner((0, '$0 @1 %1 1 0 0 80 24', ''), (0, '$0 @2 %2 2 0 0 40 24', ''))
        out = terminals.focus(self.handle('%2'), self.handle('%1'), run=run)
        self.assertEqual(out['status'], 'geometry')
        self.assertFalse(any('select-window' in argv for argv, _ in run.calls))


@unittest.skipUnless(shutil.which('tmux'), 'tmux not installed')
class TmuxLiveTests(unittest.TestCase):
    """A private tmux server on its own socket: no terminal app is involved or touched."""
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='hui-', dir='/tmp'); self.addCleanup(shutil.rmtree, self.dir, True)
        self.socket = str(Path(self.dir)/'s')
        self.addCleanup(lambda: subprocess.run(['tmux', '-S', self.socket, 'kill-server'], capture_output=True))
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': self.dir, 'TERM': 'xterm-256color', 'SHELL': '/bin/sh'}
        self.env = env
        self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'alpha', '-x', '100', '-y', '30', 'sleep 300')
        self.tmux('new-window', '-t', 'alpha:', 'sleep 300')
        self.tmux('new-window', '-t', 'alpha:', 'sleep 300')
        self.tmux('split-window', '-h', '-t', 'alpha:2', 'sleep 300')
        self.tmux('new-session', '-d', '-s', 'beta', '-x', '100', '-y', '30', 'sleep 300')
        self.tmux('select-window', '-t', 'alpha:0')

    def tmux(self, *args):
        return subprocess.run(['tmux', '-S', self.socket, *args], capture_output=True, text=True, check=True, env=self.env).stdout.strip()

    def pane(self, target):
        return {'kind': 'tmux', 'socket': self.socket, 'pane': self.tmux('display-message', '-p', '-t', target, '#{pane_id}')}

    def active_window(self):
        return self.tmux('display-message', '-p', '-t', 'alpha', '#{window_index}')

    def test_switches_to_a_same_shaped_tab_in_the_same_session(self):
        out = terminals.focus(self.pane('alpha:1'), self.pane('alpha:0'))
        self.assertEqual(out['status'], 'focused', out)
        self.assertEqual(self.active_window(), '1')

    def test_refusals_leave_the_active_window_alone(self):
        caller = self.pane('alpha:0')
        split = self.tmux('list-panes', '-t', 'alpha:2', '-F', '#{pane_id}').split()[0]
        cases = [(self.pane('beta:0'), {}, 'other-window'),
                 ({'kind': 'tmux', 'socket': self.socket, 'pane': split}, {}, 'geometry'),
                 ({'kind': 'tmux', 'socket': self.socket, 'pane': split}, {'switch_panes': False}, 'disabled'),
                 (self.pane('alpha:1'), {'switch_tabs': False}, 'disabled'),
                 ({'kind': 'tmux', 'socket': self.socket, 'pane': '%999'}, {}, 'missing'),
                 (caller, {}, 'already')]
        for target, options, status in cases:
            self.assertEqual(terminals.focus(target, caller, **options)['status'], status, (target, options))
            self.assertEqual(self.active_window(), '0')

    def test_a_closed_pane_is_missing_and_nothing_else_is_focused(self):
        target = self.pane('alpha:1')
        self.tmux('kill-window', '-t', 'alpha:1')
        time.sleep(0.05)
        self.assertEqual(terminals.focus(target, self.pane('alpha:0'))['status'], 'missing')
        self.assertEqual(self.active_window(), '0')


if __name__ == '__main__':
    unittest.main()
