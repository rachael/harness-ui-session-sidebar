import json
import os
from pathlib import Path
import pty
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_ui_mods import sessions, terminals
from codex_ui_mods.core import Manager, atomic_json

GUID = '0F0E0D0C-1111-4222-8333-444455556666'
CALLER = {'kind': 'iterm2', 'id': 'AAAABBBB-1111-4222-8333-444455556666'}
START = 'Mon Mar  2 09:00:00 2026'


class Table:
    """A fictional process table for ps."""
    def __init__(self, **pids):
        self.pids = {int(k[1:]): v for k, v in pids.items()}

    def __call__(self, pid):
        return self.pids.get(int(pid))


def proc(ppid=1, tty='/dev/ttys010', start=START, command='claude'):
    return {'ppid': ppid, 'tty': tty, 'start': ' '.join(start.split()), 'command': command}


class Runner:
    def __init__(self, answer=(0, 'focused', '')):
        self.answer, self.calls = answer, []

    def __call__(self, argv, stdin=None):
        self.calls.append(argv)
        return self.answer


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'state'
        env = patch.dict(os.environ, {'HARNESS_UI_HOME': str(Path(temporary.name)/'home'), 'HARNESS_UI_STATE_DIR': str(self.root),
                                      'PATH': os.environ.get('PATH', '/usr/bin:/bin')}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.iterm = {'TERM_PROGRAM': 'iTerm.app', 'ITERM_SESSION_ID': 'w0t1p0:'+GUID}

    def record(self, provider='claude', session='fixture-a'):
        return json.loads((self.root/'sessions'/provider/(session+'.json')).read_text())

    def test_announce_merges_into_the_shared_record_and_keeps_its_content(self):
        atomic_json(self.root/'sessions/claude/fixture-a.json', {'title': 'Lighthouse refactor', 'surfaced': [{'id': 'x'}], 'cwd': '/home/example/lighthouse'})
        out = sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table(p42=proc()), now=100.0)
        self.assertEqual(out['terminal'], 'iterm2')
        record = self.record()
        self.assertEqual(record['title'], 'Lighthouse refactor')
        self.assertEqual(record['surfaced'], [{'id': 'x'}])
        self.assertEqual(record['terminal']['handle'], {'kind': 'iterm2', 'id': GUID})
        self.assertEqual((record['terminal']['pid'], record['terminal']['start']), (42, ' '.join(START.split())))
        sessions.forget('claude', 'fixture-a')
        # a merge cannot remove a key: the field is cleared, and the session no longer listed
        self.assertIsNone(self.record()['terminal'])
        self.assertEqual(self.record()['title'], 'Lighthouse refactor')
        self.assertEqual(sessions.list_sessions(ps=Table(p42=proc()))['sessions'], [])

    def announced(self, provider, session, pid, record):
        atomic_json(self.root/'sessions'/provider/(session+'.json'), record)
        sessions.announce(provider, session, pid=pid, environ=self.iterm, ps=Table(**{'p'+str(pid): proc()}))

    def test_activity_comes_only_from_the_harness_events_in_the_record(self):
        started = sessions.started_at(START)*1000
        self.announced('claude', 'fixture-a', 42, {'activity': {'status': 'working', 'needs_input': True, 'updatedAt': started+5000,
                                                                 'source': 'native-event'}})
        self.announced('claude', 'fixture-b', 43, {})
        self.announced('claude', 'fixture-c', 44, {'activity': {'status': 'busy', 'updatedAt': started+5000}})
        self.announced('claude', 'fixture-d', 45, {'activity': {'status': 'idle', 'needs_input': False, 'updatedAt': started-60000}})
        self.announced('claude', 'fixture-e', 46, {'activity': {'status': 'idle', 'updatedAt': True}})
        table = Table(p42=proc(), p43=proc(), p44=proc(), p45=proc(), p46=proc())
        rows = {row['session']: row['activity'] for row in sessions.list_sessions(ps=table)['sessions']}
        self.assertEqual(rows['fixture-a'], {'status': 'working', 'needs_input': True, 'updated_at': started/1000+5})
        unknown = {'status': 'unknown', 'needs_input': False, 'updated_at': None}
        # never recorded; malformed; from before this process started (a resumed session's last run); not a time
        self.assertEqual([rows[s] for s in ('fixture-b', 'fixture-c', 'fixture-d', 'fixture-e')], [unknown]*4)

    def test_activity_is_unknown_when_the_process_start_time_cannot_be_read(self):
        record = {'terminal': {'schema': 1, 'start': 'not a time'}, 'activity': {'status': 'working', 'updatedAt': 10**13}}
        self.assertEqual(sessions.activity(record)['status'], 'unknown')
        self.assertIsNone(sessions.started_at(None))

    def test_list_groups_by_harness_newest_first_in_each_and_hides_what_is_asked(self):
        for name, provider, pid in (('fixture-a', 'claude', 42), ('fixture-b', 'codex', 43), ('fixture-c', 'claude', 44), ('fixture-d', 'pi', 45)):
            self.announced(provider, name, pid, {})
            time.sleep(0.02)
        table = Table(p42=proc(), p43=proc(), p44=proc(), p45=proc())
        rows = sessions.list_sessions(ps=table)['sessions']
        self.assertEqual([(r['provider'], r['session'], r['badge']) for r in rows],
                         [('claude', 'fixture-c', '✳'), ('claude', 'fixture-a', '✳'), ('codex', 'fixture-b', 'C'), ('pi', 'fixture-d', 'P')])
        self.assertEqual([r['provider'] for r in sessions.list_sessions(ps=table, hidden={'codex', 'pi'})['sessions']], ['claude', 'claude'])
        with patch.dict(os.environ, {'HARNESS_UI_BADGES': '{"codex": "◎"}'}):
            self.assertEqual(sessions.list_sessions(ps=table)['sessions'][2]['badge'], '◎')
        with patch.dict(os.environ, {'HARNESS_UI_BADGES': 'not json'}):
            self.assertEqual(sessions.list_sessions(ps=table)['sessions'][2]['badge'], 'C')

    def test_announce_refuses_a_process_that_is_not_running(self):
        with self.assertRaises(sessions.Error):
            sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table())

    def test_explicit_manager_keeps_presence_in_its_publisher_and_shared_root(self):
        manager = Manager(self.root.parent/'custom-manager')
        published = self.root/'sessions/claude/fixture-a.json'
        publisher = self.root.parent/'publisher.py'
        publisher.write_text(
            'import json,os,pathlib,sys\n'
            f'p=pathlib.Path({str(published)!r});p.parent.mkdir(parents=True,exist_ok=True)\n'
            'old=json.loads(p.read_text()) if p.exists() else {"revision":"0"}\n'
            'assert os.environ["HARNESS_UI_EXPECT_REVISION"] == (old["revision"] if p.exists() else "")\n'
            'p.write_text(json.dumps({"schema_version":1,"revision":str(int(old["revision"])+1),"data":json.load(sys.stdin)}))\n')
        atomic_json(manager.root/'modules/data/module.json', {
            'schema': 1, 'id': 'data', 'version': '1', 'description': 'Fixture publisher', 'codex': ['*'], 'order': 1,
            'dependencies': [], 'patches': [], 'tests': [], 'runtime_env': {
                'HARNESS_UI_STATE_DIR': str(self.root),
                'HARNESS_UI_PUBLISH_COMMAND': json.dumps([sys.executable, str(publisher)]),
                'HARNESS_UI_BADGES': json.dumps({'claude': 'X'})}})
        manager.config['enabled'] = ['data']; manager.save()
        with patch.dict(os.environ, {'HARNESS_UI_HOME': str(self.root.parent/'unrelated')}, clear=True):
            sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table(p42=proc()), manager=manager)
            self.assertEqual(json.loads(published.read_text())['revision'], '1')
            rows = sessions.list_sessions(manager=manager, ps=Table(p42=proc()))['sessions']
            self.assertEqual([(row['session'], row['badge']) for row in rows], [('fixture-a', 'X')])
            focused = sessions.focus_session('claude', 'fixture-a', caller=CALLER, manager=manager,
                                              ps=Table(p42=proc()), run=Runner())
            self.assertEqual(focused['status'], 'focused')
            sessions.forget('claude', 'fixture-a', manager=manager)
            value = json.loads(published.read_text())
            self.assertEqual(value['revision'], '2')
            self.assertIsNone(value['data']['terminal'])

    def test_list_reads_titles_from_records_and_hides_ended_sessions(self):
        atomic_json(self.root/'sessions/claude/fixture-a.json', {'title': 'Lighthouse refactor'})
        sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table(p42=proc()))
        sessions.announce('codex', 'fixture-b', pid=43, environ={'TERM_PROGRAM': 'Apple_Terminal'}, ps=Table(p43=proc(tty='/dev/ttys011')))
        # fixture-b's pid now belongs to another process, started later
        table = Table(p42=proc(), p43=proc(start='Tue Mar  3 10:00:00 2026'))
        rows = sessions.list_sessions(ps=table)['sessions']
        self.assertEqual([(r['session'], r['state'], r['title']) for r in rows], [('fixture-a', 'running', 'Lighthouse refactor')])
        self.assertEqual(rows[0]['terminal'], {'kind': 'iterm2', 'focusable': True})
        everything = sessions.list_sessions(ps=table, include_ended=True)['sessions']
        self.assertEqual({r['session']: r['state'] for r in everything}, {'fixture-a': 'running', 'fixture-b': 'ended'})
        self.assertFalse(next(r for r in everything if r['session'] == 'fixture-b')['terminal']['focusable'])

    def test_records_without_a_terminal_field_are_not_sessions_here(self):
        atomic_json(self.root/'sessions/claude/fixture-c.json', {'title': 'Never announced'})
        self.assertEqual(sessions.list_sessions(ps=Table())['sessions'], [])

    def test_focus_switches_through_the_adapter_with_the_callers_handle(self):
        sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table(p42=proc()))
        run = Runner()
        out = sessions.focus_session('claude', 'fixture-a', caller=CALLER, switch_panes=False, ps=Table(p42=proc()), run=run)
        self.assertEqual(out['status'], 'focused')
        self.assertEqual(run.calls[0][2:], [GUID, CALLER['id'], '1', '0'])

    def test_focus_refuses_ended_reused_and_unannounced_sessions_without_touching_a_terminal(self):
        sessions.announce('claude', 'fixture-a', pid=42, environ=self.iterm, ps=Table(p42=proc()))
        run = Runner()
        self.assertEqual(sessions.focus_session('claude', 'fixture-a', caller=CALLER, ps=Table(), run=run)['status'], 'ended')
        reused = Table(p42=proc(start='Tue Mar  3 10:00:00 2026'))
        self.assertEqual(sessions.focus_session('claude', 'fixture-a', caller=CALLER, ps=reused, run=run)['status'], 'ended')
        self.assertEqual(sessions.focus_session('claude', 'nobody', caller=CALLER, ps=Table(), run=run)['status'], 'missing')
        self.assertEqual(run.calls, [])

    def test_terminal_app_tty_handle_counts_only_while_the_harness_holds_that_tty(self):
        sessions.announce('claude', 'fixture-a', pid=42, environ={'TERM_PROGRAM': 'Apple_Terminal'}, ps=Table(p42=proc(tty='/dev/ttys010')))
        run = Runner()
        moved = Table(p42=proc(tty='/dev/ttys012'))
        out = sessions.focus_session('claude', 'fixture-a', caller={'kind': 'apple-terminal', 'tty': '/dev/ttys003'}, ps=moved, run=run)
        self.assertEqual(out['status'], 'missing')
        self.assertEqual(run.calls, [])

    def test_harness_pid_skips_shell_and_launcher_wrappers(self):
        table = Table(p30=proc(ppid=20, command='/bin/zsh'), p20=proc(ppid=10, command='-sh'), p10=proc(ppid=1, command='claude'))
        self.assertEqual(sessions.harness_pid(30, ps=table), 10)
        self.assertEqual(sessions.harness_pid(10, ps=table), 10)

    def test_invalid_identifiers_are_refused(self):
        for provider, session in (('claude', '../x'), ('../p', 'x'), ('claude', '')):
            with self.assertRaises(sessions.Error):
                sessions.announce(provider, session, pid=42, environ=self.iterm, ps=Table(p42=proc()))

    def test_cli_prints_json_and_focus_exit_code_reflects_the_outcome(self):
        import contextlib, io
        with patch.object(sessions, 'process_info', Table(p42=proc())), patch.object(sessions, 'caller_handle', lambda **_: CALLER), \
             patch.object(sessions.terminals, 'run', Runner((0, 'other-window', ''))):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(sessions.main(['sessions', 'list']), 0)
                self.assertEqual(sessions.main(['sessions', 'list', '--hide', 'codex']), 0)
                self.assertEqual(sessions.main(['sessions', 'focus', 'claude', 'nobody']), 3)
            lines = [json.loads(line) for line in buffer.getvalue().splitlines()]
        self.assertEqual(lines[0], {'schema': 1, 'sessions': []})
        self.assertEqual(lines[1], {'schema': 1, 'sessions': []})
        self.assertEqual(lines[2]['status'], 'missing')


class PtyTests(unittest.TestCase):
    """A real process with its own pseudo-terminal: no terminal app involved."""
    def test_process_table_names_the_controlling_tty_and_start_time(self):
        pid, fd = pty.fork()
        if pid == 0:
            os.write(1, (os.ttyname(0)+'\n').encode())
            time.sleep(30)
            os._exit(0)
        self.addCleanup(os.close, fd)
        try:
            name = os.read(fd, 200).decode().split()[0]
            info = sessions.process_info(pid)
            self.assertEqual(info['tty'], name)
            self.assertEqual(len(info['start'].split()), 5)
            self.assertEqual(sessions.liveness({'pid': pid, 'start': info['start']})[0], 'running')
            self.assertEqual(terminals.detect({'TERM_PROGRAM': 'Apple_Terminal'}, info['tty']), {'kind': 'apple-terminal', 'tty': name})
        finally:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        self.assertIsNone(sessions.process_info(pid))
        self.assertEqual(sessions.liveness({'pid': pid, 'start': info['start']})[0], 'ended')


if __name__ == '__main__':
    unittest.main()
