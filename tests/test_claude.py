import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Error, Manager, atomic_json
from codex_ui_mods.claude import install


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.dest = self.root/'catalog/adapters/claude-code', self.root/'native-cache/info-pane'
        atomic_json(self.root/'catalog/.claude-plugin/marketplace.json', {'name':'harness-ui-mods','plugins':[]})
        atomic_json(self.source/'.claude-plugin/plugin.json', {'name': 'info-pane', 'version': '1.0'})

    def command(self, argv, **kwargs):
        if argv[1:] == ['--version']: return '2.1.310 (Claude Code)'
        if argv[1:3] == ['plugin', 'validate']: return '{}'
        if argv[1:3] == ['plugin', 'install']:
            self.native_created = True
            return '{}'
        return json.dumps([{'id':'info-pane@harness-ui-mods','installPath':str(self.dest),'enabled':True}]) if getattr(self,'native_created',False) else '[]'

    def test_validate_before_native_install_without_a_skills_copy(self):
        with patch('codex_ui_mods.claude.run', side_effect=self.command) as run:
            result = install(self.source, None)
        self.assertTrue(result['discovered']); self.assertTrue(result['enabled'])
        self.assertEqual(result['ownership'],'native')
        self.assertEqual(run.call_args_list[2].args[0][1:3], ['plugin', 'validate'])
        command = run.call_args_list[3].args[0]
        self.assertEqual(command[1:4], ['plugin','install','info-pane'])
        self.assertEqual(command[command.index('--marketplace')+1],str((self.root/'catalog').resolve()))
        self.assertFalse(self.dest.exists())

    def test_native_update_preserves_disabled_owner_and_never_reinstalls_missing(self):
        from codex_ui_mods.claude import update_native
        entry={'id':'info-pane@harness-ui-mods','enabled':False,'installPath':str(self.dest)}
        with patch('codex_ui_mods.claude.run',side_effect=[json.dumps([entry]),'{}',json.dumps([entry])]) as run:
            self.assertFalse(update_native()['enabled'])
        self.assertEqual(run.call_args_list[1].args[0],['claude','plugin','update','info-pane@harness-ui-mods','--scope','user','--json'])
        with patch('codex_ui_mods.claude.run',return_value='[]') as run:
            with self.assertRaisesRegex(Error,'missing or changed'):update_native()
        self.assertEqual(run.call_count,1)

    def test_old_capability_or_failed_validation_changes_no_destination(self):
        with patch('codex_ui_mods.claude.run', return_value='2.1.1 (Claude Code)'):
            with self.assertRaises(Error): install(self.source, None)
        self.assertFalse(self.dest.exists())
        def invalid(argv, **kwargs):
            if argv[1:] == ['--version']: return '2.1.310'
            if argv[1:3] == ['plugin','list']: return '[]'
            raise Error('invalid plugin')
        with patch('codex_ui_mods.claude.run', side_effect=invalid):
            with self.assertRaises(Error): install(self.source, None)
        self.assertFalse(self.dest.exists())

    def test_ask_mode_does_not_sync_without_consent_even_no_refresh(self):
        manager = Manager(self.root/'state'); manager.config['updates'] = 'ask'
        with patch('codex_ui_mods.core.sys_stdin_yes', return_value=False), patch.object(manager, 'sync_claude') as sync:
            self.assertEqual(manager.update(refresh=False)['status'], 'pending')
        sync.assert_not_called()

    def test_native_feature_config_partial_write_and_readback_preserves_pet(self):
        from types import SimpleNamespace
        from codex_ui_mods.claude import configure_features
        before = {'schema': {'infoPane': {}, 'contextDrawer': {}, 'petEnabled': {}}, 'inputs': {'petEnabled': 'false'}}
        after = dict(before, inputs={'infoPane': 'false', 'petEnabled': 'false'})
        with patch('codex_ui_mods.claude.run', side_effect=[json.dumps(before), json.dumps(after)]), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stderr='')) as execute:
            self.assertTrue(configure_features({'info-pane': False})['verified'])
        self.assertEqual(json.loads(execute.call_args.kwargs['input']), {'infoPane': 'false'})
        self.assertIn('--values-stdin', execute.call_args.args[0])

    def test_unknown_pet_option_does_not_write_any_configuration(self):
        from codex_ui_mods.claude import configure_features
        with patch('codex_ui_mods.claude.run', return_value='{"schema":{"infoPane":{}}}'), patch('subprocess.run') as execute:
            with self.assertRaisesRegex(Error, 'does not expose'): configure_features({'pet': True})
        execute.assert_not_called()

    def test_claude_only_update_and_check_never_execute_stock(self):
        manager = Manager(self.root/'home')
        manager.config.update(stock=str(self.root/'missing-codex'), claude_install={'destination':str(self.dest), 'command':'claude'})
        with patch.object(manager, 'sync_claude', return_value={'status':'unchanged'}) as sync, \
             patch.object(manager, 'stock', side_effect=AssertionError('must not execute Codex')):
            result = manager.update(refresh=False)
            check = manager.check()
        self.assertEqual(result['harness'], 'claude'); self.assertEqual(result['status'], 'current')
        self.assertIsNone(check['stock']); self.assertTrue(check['claude']['configured'])
        sync.assert_called_once()
        manager.config['updates'] = 'ask'; manager.save()
        with patch('codex_ui_mods.core.sys_stdin_yes', return_value=False), \
             patch.object(Manager, 'stock', side_effect=AssertionError('must not execute Codex')), \
             patch.object(Manager, 'availability', return_value={'metadata_only':True}), patch('builtins.print'):
            from codex_ui_mods.cli import main
            self.assertEqual(main(['--home', str(manager.root), 'update']), 2)
            self.assertEqual(main(['--home', str(manager.root), 'check']), 0)
