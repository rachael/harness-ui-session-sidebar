import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_ui_mods import sessions, sidebar

def activity(status='unknown', needs_input=False):
    return {'status': status, 'needs_input': needs_input, 'updated_at': None if status == 'unknown' else 990.0}


ROWS = [
    {'provider': 'claude', 'badge': '✳', 'session': 'fixture-a', 'state': 'running', 'title': 'Lighthouse refactor',
     'cwd': '/home/example/lighthouse', 'branch': 'main', 'model': '', 'updated_at': 1000.0, 'activity': activity('working'),
     'terminal': {'kind': 'tmux', 'focusable': True}},
    {'provider': 'codex', 'badge': 'C', 'session': 'fixture-b', 'state': 'unknown', 'title': '', 'cwd': '/home/example/harbor',
     'branch': '', 'model': '', 'updated_at': 400.0, 'activity': activity('idle', needs_input=True),
     'terminal': {'kind': 'none', 'focusable': False}},
]


class DrawingTests(unittest.TestCase):
    def test_rows_are_grouped_under_each_harness_fit_the_width_and_keep_the_note_row(self):
        drawn = sidebar.lines(ROWS, set(), '', 32, 1030.0, '/home/example')
        self.assertEqual([kind for _, kind, _ in drawn], ['heading', 'note', 'group', 'row', 'group', 'row'])
        self.assertEqual(drawn[1][0], ' ')
        self.assertEqual([(text, key) for text, kind, key in drawn if kind == 'group'], [('✳ Claude Code', 'claude'), ('C Codex', 'codex')])
        self.assertTrue(all(len(text) <= 32 for text, _, _ in drawn))
        self.assertTrue(drawn[3][0].startswith('[+]● Lighthouse refactor'))
        self.assertTrue(drawn[3][0].endswith(' now'))
        # idle, but something still waits on the person
        self.assertTrue(drawn[5][0].startswith('[+]? ~/harbor'))
        self.assertTrue(drawn[5][0].endswith(' 10m'))

    def test_each_activity_has_one_glyph_and_its_words(self):
        cases = [(activity('working'), '●', 'working'), (activity('idle'), '○', 'idle'),
                 (activity('awaiting_input', True), '?', 'waiting on you'),
                 (activity('working', True), '?', 'working, waiting on you'),
                 (activity('idle', True), '?', 'idle, waiting on you'), (activity(), '·', 'activity unknown')]
        for value, glyph, words in cases:
            self.assertEqual((sidebar.glyph(value), sidebar.words(value)), (glyph, words))

    def test_details_only_under_their_own_toggle(self):
        drawn = sidebar.lines(ROWS, {'codex:fixture-b'}, 'Not switched: gone', 32, 1030.0, '/home/example')
        self.assertEqual([kind for _, kind, _ in drawn], ['heading', 'note', 'group', 'row', 'group', 'row', 'detail', 'detail', 'detail'])
        self.assertTrue(drawn[5][0].startswith('[-]'))
        self.assertTrue(drawn[6][0].startswith('    idle, waiting on you · proc'))
        self.assertIn('none: cannot switch', drawn[8][0])

    def test_clicks_toggle_on_the_control_and_switch_on_the_row_never_on_a_heading(self):
        drawn = sidebar.lines(ROWS, set(), '', 32, 1030.0)
        self.assertEqual(sidebar.hit(drawn, 3, 1), ('toggle', 'claude:fixture-a'))
        self.assertEqual(sidebar.hit(drawn, 3, 10), ('switch', 'claude:fixture-a'))
        self.assertIsNone(sidebar.hit(drawn, 2, 1))
        self.assertIsNone(sidebar.hit(drawn, 0, 1))
        self.assertIsNone(sidebar.hit(drawn, 40, 1))

    def test_options_come_from_arguments_then_the_installers_environment_then_on(self):
        self.assertEqual(sidebar.choices({}), {'switch_tabs': True, 'switch_panes': True, 'hide': []})
        environ = {'HARNESS_UI_SIDEBAR_SWITCH_TABS': 'false', 'HARNESS_UI_SIDEBAR_SHOW_CODEX': '0', 'HARNESS_UI_SIDEBAR_SHOW_CLAUDE': 'yes'}
        chosen = sidebar.choices(environ)
        self.assertEqual(chosen, {'switch_tabs': False, 'switch_panes': True, 'hide': ['codex']})
        self.assertEqual(sidebar.options_argv(chosen), ['--no-switch-tabs', '--switch-panes', '--hide', 'codex'])
        self.assertTrue(sidebar.choices(environ, switch_tabs=True)['switch_tabs'])

    def test_keys_move_expand_and_queue_a_switch_without_running_it_inline(self):
        bar = sidebar.Sidebar('%1', socket='/tmp/fixture')
        bar.rows = ROWS
        self.assertTrue(bar.act('down'))
        self.assertEqual(bar.selected, 1)
        bar.act('toggle')
        self.assertEqual(bar.expanded, {'codex:fixture-b'})
        bar.act('collapse')
        self.assertEqual(bar.expanded, set())
        bar.act('switch')
        self.assertEqual(bar.requests, ['codex:fixture-b'])
        self.assertFalse(bar.act('quit'))


@unittest.skipUnless(shutil.which('tmux'), 'tmux not installed')
class LayoutLiveTests(unittest.TestCase):
    """A private tmux server: the layout and a switch from the sidebar, with no terminal app."""
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='hui-', dir='/tmp'); self.addCleanup(shutil.rmtree, self.dir, True)
        self.socket = str(Path(self.dir)/'s')
        self.state = Path(self.dir)/'state'
        env = patch.dict(os.environ, {'HARNESS_UI_HOME': str(Path(self.dir)/'home'), 'HARNESS_UI_STATE_DIR': str(self.state),
                                      'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': self.dir, 'SHELL': '/bin/sh',
                                      'TERM': 'xterm-256color'}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.addCleanup(lambda: subprocess.run(['tmux', '-S', self.socket, 'kill-server'], capture_output=True))

    def tmux(self, *args):
        return subprocess.run(['tmux', '-S', self.socket, *args], capture_output=True, text=True, check=True).stdout.strip()

    def panes(self, target):
        rows = self.tmux('list-panes', '-t', target, '-F', '#{pane_id} #{pane_left} #{pane_width} #{pane_active}').splitlines()
        return [row.split() for row in rows]

    def test_layout_puts_the_sidebar_left_and_keeps_the_harness_active(self):
        owner = sidebar.layout(['sleep', '300'], width=28, socket=self.socket, attach=False)
        session = self.tmux('display-message', '-p', '-t', owner, '#{session_id}')
        (left, left_x, left_w, left_active), (right, right_x, _, right_active) = sorted(self.panes(session), key=lambda p: int(p[1]))
        self.assertEqual((left_x, left_w, left_active), ('0', '28', '0'))
        self.assertEqual((right, right_active), (owner, '1'))
        for _ in range(40):
            if 'Sessions' in self.tmux('capture-pane', '-p', '-t', left):
                break
            time.sleep(0.1)
        self.assertIn('No sessions announced', self.tmux('capture-pane', '-p', '-t', left))

    def test_existing_server_receives_selected_shared_state_owner(self):
        self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'existing', 'sleep 300')
        self.tmux('set-environment', '-g', 'HARNESS_UI_PUBLISH_COMMAND', '["/fixture/stale-publisher"]')
        selected_home = str(Path(self.dir)/'selected-manager')
        selected_state = str(Path(self.dir)/'selected-state')
        env = dict(os.environ, HARNESS_UI_HOME=selected_home, HARNESS_UI_STATE_DIR=selected_state)
        captured = Path(self.dir)/'child-environment.json'
        script = ('import json,os,pathlib,time; '
                  f'pathlib.Path({str(captured)!r}).write_text(json.dumps({{key: os.environ.get(key) for key in '
                  '("HARNESS_UI_HOME", "HARNESS_UI_STATE_DIR", "HARNESS_UI_PUBLISH_COMMAND")})); time.sleep(30)')
        owner = sidebar.layout([sys.executable, '-c', script], socket=self.socket, environ=env, attach=False)
        session = self.tmux('display-message', '-p', '-t', owner, '#{session_id}')
        self.assertEqual(self.tmux('show-environment', '-t', session, 'HARNESS_UI_HOME'), 'HARNESS_UI_HOME='+selected_home)
        self.assertEqual(self.tmux('show-environment', '-t', session, 'HARNESS_UI_STATE_DIR'), 'HARNESS_UI_STATE_DIR='+selected_state)
        for _ in range(40):
            if captured.exists(): break
            time.sleep(0.05)
        child = json.loads(captured.read_text())
        self.assertEqual(child, {'HARNESS_UI_HOME': selected_home, 'HARNESS_UI_STATE_DIR': selected_state,
                                 'HARNESS_UI_PUBLISH_COMMAND': None})

    def test_the_sidebar_switches_to_a_same_shaped_window_beside_its_owner(self):
        self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'alpha', '-x', '120', '-y', '30', 'sleep 300')
        self.tmux('new-window', '-t', 'alpha:', 'sleep 300')
        owners = []
        for window in ('alpha:0', 'alpha:1'):
            pane = self.tmux('display-message', '-p', '-t', window, '#{pane_id}')
            environ = {'TMUX': self.socket+',1,0', 'TMUX_PANE': pane}
            owners.append(sidebar.layout(['sleep', '300'], socket=self.socket, environ=environ, attach=False))
        self.tmux('select-window', '-t', 'alpha:0')
        pid = int(self.tmux('display-message', '-p', '-t', owners[1], '#{pane_pid}'))
        sessions.announce('claude', 'fixture-a', pid=pid, environ={'TMUX': self.socket+',1,0', 'TMUX_PANE': owners[1]})
        bar = sidebar.Sidebar(owners[0], socket=self.socket)
        bar.refresh()
        self.assertEqual([row['session'] for row in bar.rows], ['fixture-a'])
        bar.switch('claude:fixture-a')
        self.assertEqual(bar.note, '')
        self.assertEqual(self.tmux('display-message', '-p', '-t', 'alpha', '#{window_index}'), '1')
        # from the other side, a pane of another size is refused and nothing moves
        self.tmux('resize-pane', '-t', owners[1], '-x', '60')
        back = sidebar.Sidebar(owners[1], socket=self.socket)
        sessions.announce('claude', 'fixture-z', pid=int(self.tmux('display-message', '-p', '-t', owners[0], '#{pane_pid}')),
                          environ={'TMUX': self.socket+',1,0', 'TMUX_PANE': owners[0]})
        back.switch('claude:fixture-z')
        self.assertTrue(back.note.startswith('Not switched:'), back.note)
        self.assertEqual(self.tmux('display-message', '-p', '-t', 'alpha', '#{window_index}'), '1')


if __name__ == '__main__':
    unittest.main()
