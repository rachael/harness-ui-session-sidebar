import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

from codex_ui_mods.codex import hook, install_hooks, update_record
from codex_ui_mods.core import Manager
from codex_ui_mods.surface import read_record, session_path, surface
from codex_ui_mods import sessions


class CodexOverviewTests(unittest.TestCase):
    def test_native_activity_separates_work_from_attention(self):
        record = update_record({}, {'session_id': 'sample', 'hook_event_name': 'SessionStart'})
        self.assertEqual(record['activity']['status'], 'unknown')
        record = update_record(record, {'session_id': 'sample', 'hook_event_name': 'UserPromptSubmit', 'prompt': 'Build it'})
        self.assertEqual(record['activity']['status'], 'working')
        event = {'session_id': 'sample', 'hook_event_name': 'PreToolUse',
                 'tool_name': 'request_user_input_async', 'tool_use_id': 'ask',
                 'tool_input': {'questions': [{'id': 'layout', 'title': 'Which layout?'}]}}
        record = update_record(record, event)
        self.assertEqual(record['activity']['status'], 'working')
        self.assertTrue(record['activity']['needs_input'])
        record = update_record(record, dict(event, tool_name='request_user_input'))
        self.assertEqual(record['activity']['status'], 'awaiting_input')
        record = update_record(record, dict(event, hook_event_name='PostToolUse',
            tool_response={'answers': {'layout': {'answers': ['compact']}}}))
        self.assertEqual(record['activity']['status'], 'working')
        self.assertFalse(record['activity']['needs_input'])
        for kind in ('Stop', 'Interrupt'):
            record = update_record(record, {'session_id': 'sample', 'hook_event_name': kind})
            self.assertEqual(record['activity']['status'], 'idle')
        self.assertEqual(record['activity']['source'], 'native-event')

    def test_explicit_questions_deduplicate_and_only_answers_resolve(self):
        event = {'session_id': 'sample', 'hook_event_name': 'PreToolUse',
                 'tool_name': 'functions.request_user_input', 'tool_use_id': 'call1',
                 'tool_input': {'questions': [{'id': 'color', 'question': 'Which color?'},
                                              {'id': 'size', 'question': 'Which size?'}]}}
        initial = update_record({}, event)
        self.assertEqual(initial, update_record(initial, event))
        accepted = dict(event, hook_event_name='PostToolUse', tool_response={'accepted': True})
        self.assertEqual(len(update_record(initial, accepted)['views']['main']['asks']), 2)
        answered = dict(accepted, tool_response={'answers': {'color': {'answers': ['blue']}}})
        remaining = update_record(initial, answered)['views']['main']['asks']
        self.assertEqual([a['text'] for a in remaining], ['Which size?'])

    def test_async_title_and_plan_and_full_answer(self):
        record = update_record({}, {'session_id': 'sample', 'hook_event_name': 'PreToolUse',
            'tool_name': 'request_user_input_async', 'tool_use_id': 'async1',
            'tool_input': {'questions': [{'title': 'Choose a layout', 'options': ['wide', 'compact']}]}})
        record = update_record(record, {'session_id': 'sample', 'hook_event_name': 'PostToolUse',
            'tool_name': 'update_plan', 'tool_input': {'plan': [{'step': 'Build the view', 'status': 'in_progress'}]}})
        answer = 'Complete answer. '*1000
        event = {'session_id': 'sample', 'hook_event_name': 'Stop', 'last_assistant_message': answer}
        final = update_record(record, event)
        self.assertEqual(final['views']['main']['answer'], answer)
        self.assertEqual(final, update_record(final, event))
        self.assertEqual(final['views']['main']['asks'][0]['text'], 'Choose a layout')
        self.assertEqual(final['views']['main']['tasks'][0]['subject'], 'Build the view')

    def test_real_shared_publication_preserves_confirmed_items(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'HARNESS_UI_STATE_DIR': temp}, clear=True):
            manager = Manager(Path(temp)/'manager')
            manager.config.update(scan_paths=[str(Path(__file__).resolve().parents[1]/'modules')], enabled=['info-pane'])
            surface('note', 'sample', 'Remember this', 'response:1', manager=manager)
            event = {'session_id': 'sample', 'hook_event_name': 'Stop', 'last_assistant_message': 'New answer'}
            self.assertTrue(hook(event, manager)['changed'])
            path = session_path(Path(temp), 'codex', 'sample')
            original = path.read_bytes()
            self.assertFalse(hook(event, manager)['changed'])
            self.assertEqual(path.read_bytes(), original)
            record, _ = read_record(path, 'sample')
            self.assertTrue(record['surfaced'][0]['verified'])
            self.assertEqual(record['views']['main']['answer'], 'New answer')

    def test_disabled_hooks_do_not_create_state_or_probe_processes(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {}, clear=True):
            manager = Manager(Path(temp)/'manager')
            with patch('codex_ui_mods.codex.announce') as announce:
                result = hook({'session_id': 'sample', 'hook_event_name': 'SessionStart'}, manager)
            self.assertFalse(result['changed'])
            announce.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_sidebar_start_announces_through_shared_writer_outside_overview_lock(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'HARNESS_UI_STATE_DIR': temp}, clear=True):
            manager = Manager(Path(temp)/'manager')
            # This tests the producer independently of the installer's module metadata.
            with patch.object(manager, 'selection', return_value=[{'id': 'session-sidebar'}]):
                process = {'ppid': 1, 'tty': '/dev/ttys010', 'start': 'Mon Oct 26 12:00:00 2026', 'command': 'codex'}
                def real_announce(*args, **kwargs):
                    return sessions.announce(*args, **kwargs, pid=42, ps=lambda pid: process)
                surface('note', 'sample', 'Keep this note', 'response:1', manager=manager)
                with patch('codex_ui_mods.codex.announce', side_effect=real_announce):
                    hook({'session_id': 'sample', 'hook_event_name': 'SessionStart'}, manager)
                record, _ = read_record(session_path(Path(temp), 'codex', 'sample'), 'sample')
                self.assertEqual(record['terminal']['pid'], 42)
                self.assertEqual(record['activity']['status'], 'unknown')
                self.assertTrue(record['surfaced'][0]['verified'])
                rows = sessions.list_sessions(root=temp, ps=lambda pid: process)['sessions']
                self.assertEqual([(row['provider'], row['session']) for row in rows], [('codex', 'sample')])

    def test_installer_preserves_unrelated_hooks_and_does_not_write_on_check(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'hooks.json'
            original = {'other': True, 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'example-existing-hook'}]}]}}
            path.write_text(json.dumps(original))
            before = path.read_bytes()
            self.assertTrue(install_hooks(path, apply=False)['changed'])
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(install_hooks(path)['applied'])
            current = path.read_bytes()
            self.assertFalse(install_hooks(path)['changed'])
            self.assertEqual(path.read_bytes(), current)
            self.assertEqual(json.loads(current)['hooks']['Stop'][0], original['hooks']['Stop'][0])
            self.assertEqual(next(Path(temp).glob('*.before-harness-ui-*')).read_bytes(), before)

    def test_installer_upgrades_only_its_legacy_hook_command(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'hooks.json'
            legacy = {'type': 'command', 'command': shlex.join([sys.executable, '-m', 'codex_ui_mods.codex', 'hook'])}
            foreign = {'type': 'command', 'command': 'example-existing-hook'}
            path.write_text(json.dumps({'hooks': {'Stop': [{'hooks': [legacy, foreign]}]}}))
            install_hooks(path)
            groups = json.loads(path.read_text())['hooks']['Stop']
            self.assertEqual(groups[0]['hooks'], [foreign])
            self.assertEqual(shlex.split(groups[1]['hooks'][0]['command'])[1:],
                             ['-P', '-m', 'codex_ui_mods.codex', 'hook'])
            self.assertFalse(install_hooks(path)['changed'])

    def test_hook_import_uses_installed_package_instead_of_current_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = {'HOME': str(root/'home'), 'PATH': os.environ.get('PATH', '')}
            virtual = root/'venv'
            venv.EnvBuilder(with_pip=False).create(virtual)
            python = virtual/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            site = Path(subprocess.check_output(
                [str(python), '-P', '-c', 'import site;print(site.getsitepackages()[0])'],
                text=True, env=environment).strip())
            (site/'harness-ui-source.pth').write_text(str(Path(__file__).resolve().parents[1]/'src')+'\n')
            cwd = root/'project'; package = cwd/'codex_ui_mods'; package.mkdir(parents=True)
            marker = root/'project-package-ran'
            (package/'__init__.py').write_text(f'from pathlib import Path; Path({str(marker)!r}).touch()\n')
            (package/'codex.py').write_text('')
            hooks = root/'hooks.json'; install_hooks(hooks, python=str(python))
            command = shlex.split(json.loads(hooks.read_text())['hooks']['Stop'][0]['hooks'][0]['command'])
            event = json.dumps({'session_id': 'fixture-safe', 'hook_event_name': 'Stop'})
            result = subprocess.run(command, input=event, cwd=cwd, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            # The same benign fixture demonstrates the old command's shadowing.
            old = [part for part in command if part != '-P']
            subprocess.run(old, input=event, cwd=cwd, env=environment, capture_output=True, text=True, check=True)
            self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
