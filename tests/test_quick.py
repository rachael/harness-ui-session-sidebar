import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Manager, atomic_json
from codex_ui_mods.quick import prepare


class QuickInstallTests(unittest.TestCase):
    def test_defaults_detect_harnesses_select_builtins_and_do_not_configure_pets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manager = Manager(root/'home')
            plugin = root/'home/catalog/modules/fixture'; plugin.mkdir(parents=True)
            atomic_json(plugin/'module.json', {'schema': 1, 'id': 'fixture', 'version': '1', 'description': 'Fixture',
                        'codex': ['*'], 'order': 1, 'dependencies': [], 'patches': [], 'tests': []})
            def which(name): return '/fixture/'+name
            with patch('codex_ui_mods.quick.shutil.which', side_effect=which), patch.object(manager, 'stock'), \
                 patch.object(manager, 'update', return_value={'status': 'installed'}) as update, \
                 patch('codex_ui_mods.quick.install_claude', return_value={'status': 'installed'}) as install:
                result = prepare(manager, claude_destination=root/'skills/info-pane')
            self.assertEqual(result['updates'], 'ask')
            self.assertEqual(manager.config['enabled'], ['fixture'])
            update.assert_called_once_with(yes=True, refresh=False, include_claude=False)
            self.assertEqual(set(result['harnesses']), {'codex', 'claude'})
            self.assertNotIn('pet', manager.config)
            self.assertEqual(install.call_args.args[2], 'claude')

    def test_policy_override_and_explicit_disables_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary)/'home'); manager.config['disabled'] = ['fixture']
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/codex'), patch.object(manager, 'stock'), \
                 patch.object(manager, 'modules', return_value={}), patch.object(manager, 'update', return_value={'status': 'stock'}):
                result = prepare(manager, harness='codex', updates='auto')
            self.assertEqual(result['updates'], 'auto')
            self.assertEqual(manager.config['disabled'], ['fixture'])

    def test_codex_hook_cli_check_is_forwarded_without_mutation(self):
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as temporary, patch('codex_ui_mods.codex.install_hooks', return_value={'changed':False}) as install, patch('builtins.print'):
            self.assertEqual(main(['--home', temporary, 'codex', 'install-hooks', '--check']), 0)
        install.assert_called_once_with(path=None, apply=False, python=None)

    def test_selected_codex_info_pane_installs_merged_native_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); manager = Manager(root/'home')
            folder = manager.root/'catalog/modules/info-pane'; folder.mkdir(parents=True)
            atomic_json(folder/'module.json', {'schema':1,'id':'info-pane','version':'1','description':'Fixture',
                        'codex':['*'],'order':1,'dependencies':[],'patches':[],'tests':[]})
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/codex'), patch.object(manager, 'stock'), \
                 patch.object(manager, 'update', return_value={'status':'installed'}), \
                 patch('codex_ui_mods.codex.install_hooks', return_value={'changed':True}) as install:
                result = prepare(manager, harness='codex')
            install.assert_called_once_with()
            self.assertTrue(result['harnesses']['codex']['hooks']['changed'])

    def test_sidebar_only_installs_codex_presence_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary)/'home')
            folder = manager.root/'catalog/modules/session-sidebar'; folder.mkdir(parents=True)
            atomic_json(folder/'module.json', {'schema':1,'id':'session-sidebar','version':'1','description':'Fixture',
                        'codex':['*'],'order':1,'dependencies':[],'patches':[],'tests':[]})
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/codex'), patch.object(manager, 'stock'), patch.object(manager, 'update', return_value={'status':'installed'}), patch('codex_ui_mods.codex.install_hooks', return_value={'changed':True}) as hooks:
                prepare(manager, harness='codex', modules=['session-sidebar'])
            hooks.assert_called_once_with()

    def test_native_defaults_and_all_preserve_explicit_disabled_features(self):
        for all_modules in (False,True):
            with self.subTest(all_modules=all_modules),tempfile.TemporaryDirectory() as temporary:
                manager=Manager(Path(temporary)/'home');manager.config['disabled']=['statusline','session-sidebar']
                atomic_json(manager.root/'catalog/adapters/claude-code/manifest.json',{'features':[
                    {'id':'statusline','config_key':'statusline','default':False},
                    {'id':'session-sidebar','config_key':'sessionSidebar','default':False}]})
                with patch('codex_ui_mods.quick.shutil.which',return_value='/fixture/claude'),patch('codex_ui_mods.quick.install_claude',return_value={'ownership':'native','new_install':True,'enabled':True}),patch('codex_ui_mods.claude.configure_features',return_value={}) as configure,patch('codex_ui_mods.claude_statusline.enable') as enable,patch('codex_ui_mods.claude_statusline.disable'),patch('codex_ui_mods.claude_statusline.check'):
                    prepare(manager,harness='claude',all_modules=all_modules,module_options={'session-sidebar':{'switch_tabs':True}})
                enable.assert_not_called()
                if configure.called:
                    self.assertFalse(any(configure.call_args.args[0].values()))
                    self.assertEqual(configure.call_args.kwargs['option_values'],{})
                self.assertFalse(set(manager.config['selected_modules']) & {'statusline','session-sidebar'})

    def test_sessions_layout_cli_receives_saved_sidebar_options(self):
        import os
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as temporary:
            manager=Manager(Path(temporary)/'home')
            folder=manager.root/'catalog/modules/session-sidebar';folder.mkdir(parents=True)
            atomic_json(folder/'module.json', {'schema':1,'id':'session-sidebar','version':'1','description':'Fixture',
                'codex':['*'],'order':1,'dependencies':[],'patches':[],'tests':[],
                'options':[{'id':'switch_tabs','type':'boolean','default':True,'runtime_env':'HARNESS_UI_SIDEBAR_SWITCH_TABS'}]})
            manager.config['enabled']=['session-sidebar'];manager.config['module_options']={'session-sidebar':{'switch_tabs':False}};manager.save()
            before=dict(os.environ)
            with patch('codex_ui_mods.sidebar.layout') as layout:
                self.assertEqual(main(['--home',str(manager.root),'sessions','layout','--','claude']),0)
            self.assertEqual(layout.call_args.args[0],['claude'])
            self.assertIn('--no-switch-tabs',layout.call_args.kwargs['options'])
            self.assertEqual(dict(os.environ),before)

    def test_rerun_preserves_selection_and_does_not_install_newly_detected_harness(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary)/'home')
            folder = manager.root/'catalog/modules/first'; folder.mkdir(parents=True)
            def add(name):
                module = folder.parent/name; module.mkdir(exist_ok=True)
                atomic_json(module/'module.json', {'schema':1,'id':name,'version':'1','description':'Fixture',
                            'codex':['*'],'order':1,'dependencies':[],'patches':['source.patch'],
                            'tests':[{'package':'codex-tui','filter':'fixture'}]})
                (module/'source.patch').write_text('fixture')
            add('first')
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/codex'), patch.object(manager, 'stock'), patch.object(manager, 'update', return_value={'status':'installed'}), patch('codex_ui_mods.quick.install_claude') as claude:
                prepare(manager, harness='codex')
                add('second')
                result = prepare(manager)
                claude.assert_not_called()
            self.assertEqual(manager.config['enabled'], ['first'])
            self.assertIn('codex:second', result['optional_features'])

    def test_dry_run_has_no_native_calls_network_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary)/'home')
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/command'), patch.object(manager, 'stock', side_effect=AssertionError('native call')):
                result = prepare(manager, dry_run=True)
            self.assertTrue(result['dry_run'])
            self.assertFalse(manager.root.exists())

    def test_cli_dry_run_skips_catalog_refresh_and_all_writes(self):
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)/'home'
            with patch('codex_ui_mods.quick.shutil.which', return_value='/fixture/command'), patch.object(Manager, 'refresh', side_effect=AssertionError('network')), patch('builtins.print'):
                self.assertEqual(main(['--home',str(home),'install','--dry-run']),0)
            self.assertFalse(home.exists())

    def test_no_build_install_and_update_flags_reach_manager(self):
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as directory:
            manager=Manager(Path(directory)/'home')
            with patch('codex_ui_mods.quick.shutil.which',return_value='/fixture/codex'),patch.object(manager,'stock'),patch.object(manager,'modules',return_value={}),patch.object(manager,'update',return_value={'status':'stock'}) as update:
                prepare(manager,harness='codex',no_build=True)
            self.assertTrue(update.call_args.kwargs['no_build'])
            with patch.object(Manager,'update',return_value={'status':'current'}) as update,patch('builtins.print'):
                self.assertEqual(main(['--home',str(manager.root),'update','--yes','--no-refresh','--no-build']),0)
            self.assertTrue(update.call_args.kwargs['no_build'])
