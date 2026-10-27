import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods import cli
from codex_ui_mods.core import Error, Manager, atomic_json
from codex_ui_mods.surface import resolve_state, surface, publish_session


class StateResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manager = Manager(self.base/'home')
        env = patch.dict(os.environ, {'HARNESS_UI_HOME': str(self.base/'home')}, clear=True)
        env.start(); self.addCleanup(env.stop)

    def module(self, name, root):
        folder = self.manager.root/'modules'/name
        atomic_json(folder/'module.json', {'schema':1, 'id':name, 'version':'1', 'description':'Shared fixture',
                    'codex':['*'], 'order':1, 'dependencies':[], 'patches':[], 'tests':[],
                    'runtime_env':{'HARNESS_UI_STATE_DIR':str(root)}})
        self.manager.config['enabled'].append(name)
        self.manager.save()
        return folder

    def test_claude_only_state_paths_surface_and_publish_share_module_root(self):
        root = self.base/'shared-state'; self.module('shared-data', root)
        with patch.object(Manager, 'stock', side_effect=AssertionError('must not run Codex')), \
             patch('codex_ui_mods.surface.subprocess.run', side_effect=AssertionError('must not run Cargo')):
            resolved = resolve_state('claude', self.manager)
            self.assertEqual(resolved['state_root'], str(root))
            item = surface('note', 'session', 'Shared fixture', 'message:1', provider='claude', manager=self.manager)
            incoming = self.base/'input.json'; atomic_json(incoming, {'title':'Claude fixture'})
            publish_session('claude', 'session', incoming, ['surfaced'], manager=self.manager)
        saved = json.loads((root/'sessions/claude/session.json').read_text())
        self.assertEqual(saved['surfaced'][0]['id'], item['id'])
        with patch('builtins.print') as output:
            self.assertEqual(cli.main(['--home', str(self.manager.root), 'state', 'paths', '--provider', 'claude']), 0)
        self.assertEqual(json.loads(output.call_args.args[0])['state_root'], str(root))

    def test_explicit_legacy_env_wins_module_neutral_value_and_disabled_ignored(self):
        self.module('shared-data', self.base/'module-root')
        with patch.dict(os.environ, {'CODEX_UI_STATE_DIR': str(self.base/'explicit-root')}):
            self.assertEqual(resolve_state('claude', self.manager)['state_root'], str(self.base/'explicit-root'))
        self.manager.config['disabled'] = ['shared-data']
        self.assertNotEqual(resolve_state('claude', self.manager)['state_root'], str(self.base/'module-root'))

    def test_conflicting_enabled_modules_are_refused_even_with_env(self):
        self.module('first-data', self.base/'first')
        self.module('second-data', self.base/'second')
        with patch.dict(os.environ, {'HARNESS_UI_STATE_DIR': str(self.base/'override')}):
            with self.assertRaisesRegex(Error, 'conflicting'): resolve_state('claude', self.manager)

    def test_module_publisher_argv_is_returned_and_explicit_env_preferred(self):
        folder = self.module('shared-data', self.base/'module-root')
        data = json.loads((folder/'module.json').read_text())
        data['runtime_env']['HARNESS_UI_PUBLISH_COMMAND'] = json.dumps(['publisher', '{provider}', '{session}'])
        atomic_json(folder/'module.json', data)
        self.assertEqual(resolve_state('claude', self.manager)['publisher_command'], ['publisher', 'claude', '{session}'])
        with patch.dict(os.environ, {'CODEX_UI_PUBLISH_COMMAND': '["explicit","{session}"]'}):
            self.assertEqual(resolve_state('claude', self.manager)['publisher_command'], ['explicit', '{session}'])

    def test_data_dependencies_are_resolved_without_stock_and_reject_disabled_cycles(self):
        first = self.module('entry-data', self.base/'shared')
        self.module('shared-data', self.base/'shared')
        data = json.loads((first/'module.json').read_text()); data['dependencies'] = ['shared-data']
        data['runtime_env'] = {}; atomic_json(first/'module.json', data)
        self.manager.config['enabled'] = ['entry-data']
        with patch.object(Manager, 'stock', side_effect=AssertionError('must not execute Codex')):
            self.assertEqual(resolve_state('claude', self.manager)['state_root'], str(self.base/'shared'))
        self.manager.config['disabled'] = ['shared-data']
        with self.assertRaisesRegex(Error, 'disabled'): resolve_state('claude', self.manager)
        self.manager.config['disabled'] = []
        shared = self.manager.root/'modules/shared-data/module.json'
        data = json.loads(shared.read_text()); data['dependencies'] = ['entry-data']; atomic_json(shared, data)
        with self.assertRaisesRegex(Error, 'cycle'): resolve_state('claude', self.manager)
