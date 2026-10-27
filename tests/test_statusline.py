"""Status line renderer and Claude Code statusLine slot helpers. Fictional data only; no
model calls, no transcript reads."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from codex_ui_mods import claude_statusline as slot  # noqa: E402
from codex_ui_mods import statusline as s  # noqa: E402

FIXTURES = json.loads((Path(__file__).resolve().parents[1]/'contracts'/'fixtures'/'statusline.json').read_text())
NOW = datetime(2026, 3, 4, 12, tzinfo=timezone.utc)


class Formatter(unittest.TestCase):
    def test_golden_fixtures(self):
        for case in FIXTURES['cases']:
            with self.subTest(case['name']):
                cfg = s.config({}, items=case['items'], ascii=case['ascii'], colors=False)
                self.assertEqual(s.format_status(case['data'], case['columns'], cfg, NOW, timezone.utc), case['expected'])
                if case['columns'] is not None:
                    self.assertLessEqual(len(case['expected']), case['columns'])

    def test_config_from_codex_environment_names(self):
        cfg = s.config({'HARNESS_UI_STATUS_LINE_ITEMS': 'cost, context,bogus,context', 'HARNESS_UI_STATUS_LINE_ASCII': '1', 'HARNESS_UI_STATUS_LINE_COLORS': '0'})
        self.assertEqual(cfg, {'items': ['cost', 'context'], 'ascii': True, 'colors': False})
        self.assertFalse(s.config({'NO_COLOR': ''})['colors'])
        self.assertTrue(s.config({'NO_COLOR': '', 'HARNESS_UI_STATUS_LINE_COLORS': '1'})['colors'])
        self.assertNotIn('cost', s.config({})['items'])

    def test_colors_wrap_segments_not_separators(self):
        line = s.format_status(FIXTURES['cases'][0]['data'], None, s.config({}, items=['project', 'context']), NOW, timezone.utc)
        self.assertEqual(line, '\x1b[34m~/projects/lighthouse (main*)\x1b[0m ‖ \x1b[36mctx 412K/1M 41%\x1b[0m')

    def test_numbers_are_type_safe(self):
        for bad in (None, True, 'x', -1, float('nan'), float('inf')):
            self.assertIsNone(s.number(bad))
        self.assertEqual(s.number(0), 0)
        self.assertEqual([s.count(n) for n in (0, 999, 1500, 132_000, 1_250_000)], ['0', '999', '1.5K', '132K', '1.25M'])

    def test_partial_session_says_since_when(self):
        data = dict(FIXTURES['cases'][0]['data'], counted_since=datetime(2026, 3, 4, 11, 5, tzinfo=timezone.utc))
        line = s.format_status(data, None, s.config({}, colors=False), NOW, timezone.utc)
        self.assertIn('since 11:05 1.28M uncached+out', line)


class ClaudePayload(unittest.TestCase):
    PAYLOAD = {
        'session_id': 'fixture-1', 'cwd': '/nowhere', 'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus 5.5'},
        'workspace': {'current_dir': '/home/example/lighthouse'}, 'effort': {'level': 'high'},
        'context_window': {'total_input_tokens': 50_000, 'context_window_size': 200_000, 'used_percentage': 25},
        'cost': {'total_cost_usd': 0}, 'rate_limits': {'five_hour': {'used_percentage': 40, 'resets_at': '2026-03-04T14:00:00Z'}},
    }

    def test_native_fields_normalize(self):
        d = s.claude_data(self.PAYLOAD, home='/home/example')
        self.assertEqual((d['cwd'], d['model'], d['effort'], d['tier'], d['cost']), ('/home/example/lighthouse', 'claude-opus-5-5', 'high', '', 0))
        self.assertEqual(d['windows'], [{'kind': 'five_hour', 'used': 40, 'resets_at': '2026-03-04T14:00:00Z'}])
        self.assertIsNone(d['tokens'])
        listed = s._windows([{'kind': 'seven_day', 'percentUsed': 5}])
        self.assertEqual(listed, [{'kind': 'seven_day', 'used': 5, 'resets_at': None}])
        self.assertEqual(s._windows('nonsense'), [])

    def test_render_reads_collector_totals_and_never_scans(self):
        with tempfile.TemporaryDirectory() as root:
            sessions = Path(root)/'sessions'/'claude'
            sessions.mkdir(parents=True)
            totals = {'schema': 1, 'startedAt': 0, 'since': 0, 'input': 900, 'output': 100, 'cacheRead': 9000, 'cacheWrite': 100}
            (sessions/'fixture-1.json').write_text(json.dumps({'schema_version': 1, 'revision': '3', 'data': {'title': 'x', 'usage': totals}}))
            payload = dict(self.PAYLOAD, transcript_path=str(Path(root)/'must-not-be-read.jsonl'))
            cfg = s.config({}, items=list(s.KNOWN_ITEMS), colors=False)
            line = s.format_status(s.claude_data(payload, s.read_usage(root, 'claude', 'fixture-1'), home='/home/example'), None, cfg, NOW, timezone.utc)
            self.assertEqual(line, '~/lighthouse ‖ Opus 5.5 · high ‖ ctx 50K/200K 25% ‖ 5h 60% left reset 14:00 ‖ sess 1K uncached+out · cache read 90% · $0.00')
            self.assertIsNone(s.read_usage(root, 'claude', '../escape'))
            self.assertIsNone(s.read_usage(root, 'claude', 'absent'))

    def test_bad_stdin_still_renders(self):
        self.assertIsInstance(s.render('claude', 'not json', state_root_path='/nonexistent', git=False, environ={}), str)
        with self.assertRaises(ValueError):
            s.render('codex', '{}')


class Slot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.settings = self.root/'settings.json'
        self.home = self.root/'home'
        self.custom = {'type': 'command', 'command': "printf 'their line\\n'"}

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        self.settings.write_text(json.dumps(data, indent=2) + '\n')

    def read(self):
        return json.loads(self.settings.read_text())

    def test_enable_twice_is_idempotent_and_disable_restores_exactly(self):
        self.write({'theme': 'dark', 'statusLine': self.custom, 'hooks': {'x': [1]}})
        first = slot.enable(self.home, settings=self.settings)
        self.assertEqual(first, {'state': 'enabled', 'changed': True, 'mode': 'extend'})
        installed = self.read()['statusLine']
        self.assertEqual(slot.enable(self.home, settings=self.settings), {'state': 'enabled', 'changed': False, 'mode': 'extend'})
        self.assertEqual(slot.enable(self.home, settings=self.settings, mode='extend')['changed'], False)
        self.assertEqual(self.read()['statusLine'], installed)
        self.assertEqual(slot.check(self.settings, self.home)['state'], 'enabled')
        # an unrelated settings change survives the restore
        data = self.read()
        data['theme'] = 'light'
        self.write(data)
        self.assertEqual(slot.disable(self.home, settings=self.settings)['changed'], True)
        self.assertEqual(self.read(), {'theme': 'light', 'statusLine': self.custom, 'hooks': {'x': [1]}})
        self.assertFalse(slot.backup_path(self.home).exists())

    def test_already_native_command_is_not_wrapped_again(self):
        native = slot.command_for('harness-ui', self.home)
        self.write({'statusLine': native})
        self.assertEqual(slot.enable(self.home, settings=self.settings)['changed'], False)
        self.assertFalse(slot.backup_path(self.home).exists())

    def test_edited_managed_command_is_never_overwritten(self):
        self.write({'statusLine': self.custom})
        slot.enable(self.home, settings=self.settings)
        data = self.read()
        data['statusLine'] = {'type': 'command', 'command': 'my-new-line'}
        self.write(data)
        with self.assertRaises(slot.Error):
            slot.disable(self.home, settings=self.settings)
        self.assertEqual(self.read()['statusLine']['command'], 'my-new-line')
        self.assertTrue(slot.backup_path(self.home).exists())
        self.assertEqual(slot.check(self.settings, self.home)['state'], 'modified')

    def test_detect_and_auto_mode(self):
        self.write({})
        self.assertEqual(slot.detect(self.settings)['auto'], 'replace')
        self.write({'statusLine': self.custom})
        self.assertEqual(slot.detect(self.settings), {'settings': str(self.settings), 'existing': 'custom', 'auto': 'extend'})
        # auto never sets a custom line aside: it extends it
        self.assertEqual(slot.enable(self.home, settings=self.settings)['mode'], 'extend')
        self.assertEqual(slot.detect(self.settings)['existing'], 'ours')

    def test_basic_setup_gets_the_full_layout(self):
        self.write({})
        self.assertEqual(slot.enable(self.home, settings=self.settings)['mode'], 'replace')
        backup = json.loads(slot.backup_path(self.home).read_text())
        self.assertIsNone(backup['items'])  # the full default layout
        slot.disable(self.home, settings=self.settings)
        self.assertEqual(self.read(), {})

    def test_explicit_replace_sets_theirs_aside_and_switching_mode_is_in_place(self):
        self.write({'statusLine': self.custom})
        self.assertEqual(slot.enable(self.home, settings=self.settings, mode='replace')['mode'], 'replace')
        payload = json.dumps({'session_id': 'x', 'effort': {'level': 'high'}})
        self.assertEqual(slot.compose(payload, slot.backup_path(self.home)), '')  # theirs not run
        installed = self.read()['statusLine']
        self.assertEqual(slot.enable(self.home, settings=self.settings, mode='extend'), {'state': 'enabled', 'changed': True, 'mode': 'extend'})
        self.assertEqual(self.read()['statusLine'], installed)  # never nested
        self.assertEqual(slot.compose(payload, slot.backup_path(self.home)), 'their line')
        slot.disable(self.home, settings=self.settings)
        self.assertEqual(self.read(), {'statusLine': self.custom})

    def test_extension_order_items_and_bounded_compose(self):
        self.write({'statusLine': self.custom})
        slot.enable(self.home, settings=self.settings)
        backup = json.loads(slot.backup_path(self.home).read_text())
        self.assertEqual(backup['items'], list(slot.COMPOSE_ITEMS))
        payload = json.dumps({'session_id': 'x', 'effort': {'level': 'high'}, 'rate_limits': {'five_hour': {'used_percentage': 10}}})
        out = slot.render(payload, slot.backup_path(self.home), state_root=str(self.root/'state'), environ={'NO_COLOR': '1'})
        self.assertEqual(out, 'their line\nhigh ‖ 5h 90% left')
        # their choice of items wins over the extension default
        out = slot.render(payload, slot.backup_path(self.home), state_root=str(self.root/'state'), environ={'NO_COLOR': '1', 'HARNESS_UI_STATUS_LINE_ITEMS': 'limits'})
        self.assertEqual(out, 'their line\n5h 90% left')
        # a slow or failing command of theirs drops out; ours still shows
        backup['original'] = {'type': 'command', 'command': 'sleep 5'}
        slot.backup_path(self.home).write_text(json.dumps(backup))
        self.assertEqual(slot.compose(payload, slot.backup_path(self.home), timeout=0.2), '')
        backup['original'] = {'type': 'command', 'command': 'cat'}
        slot.backup_path(self.home).write_text(json.dumps(backup))
        self.assertEqual(slot.compose(payload, slot.backup_path(self.home)), payload)  # same stdin
        backup['original'] = slot.command_for('harness-ui', self.home)
        slot.backup_path(self.home).write_text(json.dumps(backup))
        self.assertEqual(slot.compose(payload, slot.backup_path(self.home)), '')  # never itself

    def test_choosing_a_mode_never_enables(self):
        self.write({'statusLine': self.custom})
        self.assertEqual(slot.set_mode(self.home, 'replace', settings=self.settings), {'state': 'custom', 'changed': False, 'mode': None})
        self.assertEqual(self.read()['statusLine'], self.custom)
        self.assertFalse(slot.backup_path(self.home).exists())
        slot.enable(self.home, settings=self.settings)
        self.assertEqual(slot.set_mode(self.home, 'replace', settings=self.settings)['mode'], 'replace')
        self.assertEqual(slot.set_mode(self.home, 'replace', settings=self.settings)['changed'], False)

    def test_installed_command_carries_home_and_old_installs_migrate(self):
        installed = slot.command_for('harness-ui', self.home)
        self.assertEqual(installed['command'].split()[:4], ['harness-ui', '--home', str(self.home), 'statusline'])
        self.write({'statusLine': self.custom, 'theme': 'dark'})
        slot.enable(self.home, settings=self.settings)
        # an install from before --home: the slot and the backup hold the old command
        old = {'type': 'command', 'command': f"harness-ui statusline render --provider claude --compose-backup {slot.backup_path(self.home)}"}
        backup = json.loads(slot.backup_path(self.home).read_text())
        slot.backup_path(self.home).write_text(json.dumps(dict(backup, installed=old)))
        self.write(dict(self.read(), statusLine=old))
        self.assertEqual(slot.enable(self.home, settings=self.settings), {'state': 'enabled', 'changed': True, 'mode': 'extend'})
        self.assertEqual(self.read()['statusLine'], installed)
        self.assertEqual(json.loads(slot.backup_path(self.home).read_text())['installed'], installed)
        self.assertEqual(slot.enable(self.home, settings=self.settings)['changed'], False)
        slot.disable(self.home, settings=self.settings)
        self.assertEqual(self.read(), {'statusLine': self.custom, 'theme': 'dark'})

    def test_failed_migration_write_keeps_backup_and_slot_consistent(self):
        from unittest.mock import patch
        self.write({'statusLine': self.custom})
        slot.enable(self.home, settings=self.settings)
        old = {'type': 'command', 'command': f"harness-ui statusline render --provider claude --compose-backup {slot.backup_path(self.home)}"}
        backup = json.loads(slot.backup_path(self.home).read_text())
        slot.backup_path(self.home).write_text(json.dumps(dict(backup, installed=old)))
        self.write(dict(self.read(), statusLine=old))
        real = slot._write_json
        def failing(path, value, like=None):
            if Path(path) == self.settings:
                raise OSError('disk full')
            return real(path, value, like)
        with patch.object(slot, '_write_json', side_effect=failing):
            with self.assertRaises(OSError):
                slot.enable(self.home, settings=self.settings)
        self.assertEqual(self.read()['statusLine'], old)
        self.assertEqual(slot.check(self.settings, self.home)['state'], 'enabled')
        slot.disable(self.home, settings=self.settings)
        self.assertEqual(self.read(), {'statusLine': self.custom})

    def test_invalid_settings_are_left_alone(self):
        self.settings.write_text('{not json')
        with self.assertRaises(slot.Error):
            slot.enable(self.home, settings=self.settings)
        self.assertEqual(self.settings.read_text(), '{not json')
        self.assertFalse(slot.backup_path(self.home).exists())


if __name__ == '__main__':
    unittest.main()
