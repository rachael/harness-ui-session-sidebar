import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Manager, atomic_json
from codex_ui_mods.quick import prepare
from codex_ui_mods.selector import defaults


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.manager = Manager(Path(self.temp.name)/'shared')
        self.names = ('info-pane', 'context-drawer', 'statusline', 'session-sidebar')
        for name in self.names:
            atomic_json(self.manager.root/'catalog/modules'/name/'module.json', {
                'schema':1,'id':name,'version':'1','description':'Fixture','codex':['*'],
                'order':1,'dependencies':[],'patches':[],'tests':[], 'default':name != 'session-sidebar'})
        self.features = {name: name in ('info-pane','context-drawer') for name in self.names}
        self.options = {'session-sidebar': {'switch_tabs':False}}
        atomic_json(self.manager.root/'catalog/adapters/claude-code/manifest.json', {'features':[
            {'id':name,'config_key':name.replace('-',''),'default':self.features[name],
             'options':[{'id':'switch_tabs','type':'boolean','default':True,'config_key':'switchTabs'}] if name=='session-sidebar' else []}
            for name in self.names]})
        self.native_created = False
        self.writes = []
        def install(*args):
            new = not self.native_created; self.native_created = True
            return {'ownership':'native','new_install':new,'enabled':True,'plugin_ref':'info-pane@harness-ui-mods'}
        def preferences(*args):
            return {'modules':[name for name,value in self.features.items() if value], 'options':self.options}
        def configure(values, **kwargs):
            self.writes.append((dict(values),kwargs.get('option_values')))
            self.features.update(values)
            for name,values in kwargs.get('option_values',{}).items():self.options.setdefault(name,{}).update(values)
            return {'verified':True}
        for target,kwargs in [
            ('codex_ui_mods.quick.shutil.which',{'side_effect':lambda name:'/fixture/'+name}),
            ('codex_ui_mods.quick.install_claude',{'side_effect':install}),
            ('codex_ui_mods.claude.native_preferences',{'side_effect':preferences}),
            ('codex_ui_mods.claude.configure_features',{'side_effect':configure}),
            ('codex_ui_mods.claude.enable_native_plugin',{}),
            ('codex_ui_mods.claude_statusline.enable',{'return_value':{'state':'enabled'}}),
            ('codex_ui_mods.claude_statusline.disable',{'return_value':{'state':'disabled'}}),
            ('codex_ui_mods.claude_statusline.check',{'return_value':{'state':'disabled'}}),
            ('codex_ui_mods.codex.install_hooks',{'return_value':{'changed':False}}),
        ]:
            mock = patch(target,**kwargs); setattr(self,target.rsplit('.',1)[-1]+'_mock',mock.start());self.addCleanup(mock.stop)
        for name,result in [('stock',{'version':'0.164.0'}),('update',{'status':'current'})]:
            mock=patch.object(self.manager,name,return_value=result);mock.start();self.addCleanup(mock.stop)

    def test_native_component_installs_add_and_repeats_do_not_write_or_enable(self):
        prepare(self.manager,harness='claude',modules=['info-pane'])
        self.assertEqual({name for name,on in self.features.items() if on},{'info-pane'})
        prepare(self.manager,harness='claude',modules=['context-drawer'])
        self.assertEqual({name for name,on in self.features.items() if on},{'info-pane','context-drawer'})
        self.assertEqual(set(self.manager.config['selected_modules']),{'info-pane','context-drawer'})
        self.writes.clear();self.features['context-drawer']=False
        result=prepare(self.manager,harness='claude',modules=['context-drawer'])
        self.assertEqual(result['selection']['already_included'],['context-drawer'])
        self.assertFalse(self.features['context-drawer']);self.assertEqual(self.writes,[])
        self.enable_native_plugin_mock.assert_not_called()

    def test_component_then_full_preserves_custom_native_options_and_off_choices(self):
        self.manager.config['disabled']=['statusline','session-sidebar']
        prepare(self.manager,harness='claude',modules=['info-pane'])
        self.features['info-pane']=False;self.writes.clear()
        prepare(self.manager,harness='claude',module_options={'session-sidebar':{'switch_tabs':True}})
        self.assertEqual(self.writes,[]);self.assertFalse(self.features['info-pane'])
        self.assertFalse(self.options['session-sidebar']['switch_tabs'])
        self.assertFalse(self.features['statusline']);self.assertFalse(self.features['session-sidebar'])
        self.assertEqual(set(self.manager.config['included_components']['claude']),set(self.names))
        self.enable_mock.assert_not_called()

    def test_repeated_component_cli_reports_original_scope_without_native_writes(self):
        import io
        from contextlib import redirect_stdout
        from codex_ui_mods.cli import main
        prepare(self.manager,harness='claude',modules=['info-pane'])
        prepare(self.manager,harness='claude',modules=['context-drawer'])
        self.features['context-drawer']=False;self.writes.clear()
        out=io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(['--home',str(self.manager.root),'install','--harness','claude','--no-refresh','--non-interactive','--module','context-drawer']),0)
        self.assertEqual(json.loads(out.getvalue())['selection']['already_included'],['context-drawer'])
        self.assertEqual(self.writes,[]);self.assertFalse(self.features['context-drawer'])

    def test_codex_then_first_claude_alias_has_separate_inclusion_scope(self):
        import io
        from contextlib import redirect_stdout
        from codex_ui_mods.cli import codex_main, claude_main
        arguments=['--home',str(self.manager.root),'install','--no-refresh','--non-interactive','--module','info-pane']
        with patch.object(Manager,'stock',return_value={'version':'0.164.0'}),patch.object(Manager,'update',return_value={'status':'current'}),redirect_stdout(io.StringIO()):
            with patch('sys.argv',['codex-ui',*arguments]):self.assertEqual(codex_main(),0)
            config=Manager(self.manager.root).config
            self.assertEqual(config['included_components']['codex'],['info-pane'])
            self.assertEqual(config['included_components']['claude'],[])
            with patch('sys.argv',['claude-ui',*arguments]):self.assertEqual(claude_main(),0)
        self.assertEqual({name for name,on in self.features.items() if on},{'info-pane'})
        self.assertEqual(Manager(self.manager.root).config['included_components']['claude'],['info-pane'])

    def test_existing_native_owner_first_seen_keeps_its_off_choices(self):
        self.native_created=True
        self.features={name:False for name in self.names}
        result=prepare(self.manager,harness='claude',modules=['info-pane'])
        self.assertEqual(result['selection']['already_included'],['info-pane'])
        self.assertEqual(self.writes,[]);self.assertFalse(self.features['info-pane'])
        prepare(self.manager,harness='claude',modules=['info-pane'],module_changes=['info-pane'])
        self.assertTrue(self.features['info-pane'])

    def test_codex_additive_replace_and_actual_checkbox_removals(self):
        prepare(self.manager,harness='codex',modules=['info-pane'])
        prepare(self.manager,harness='codex',modules=['context-drawer'])
        self.assertEqual(set(self.manager.config['enabled']),{'info-pane','context-drawer'})
        prepare(self.manager,harness='codex',modules=['context-drawer'],module_changes=['info-pane'])
        self.assertEqual(self.manager.config['enabled'],['context-drawer'])
        prepare(self.manager,harness='codex',modules=['info-pane'])
        self.assertEqual(self.manager.config['enabled'],['context-drawer'])
        prepare(self.manager,harness='codex',modules=['info-pane'],replace_selection=True)
        self.assertEqual(self.manager.config['enabled'],['info-pane'])
        self.assertIn('context-drawer',self.manager.config['disabled'])

    def test_native_replace_and_checkbox_removal_are_intentional(self):
        prepare(self.manager,harness='claude',modules=['info-pane'])
        prepare(self.manager,harness='claude',modules=['context-drawer'])
        prepare(self.manager,harness='claude',modules=['context-drawer'],module_changes=['info-pane'])
        self.assertFalse(self.features['info-pane']);self.assertTrue(self.features['context-drawer'])
        prepare(self.manager,harness='claude',modules=['info-pane'],replace_selection=True)
        self.assertTrue(self.features['info-pane']);self.assertFalse(self.features['context-drawer'])
        self.disable_mock.assert_called()

    def test_selector_preserves_other_components_but_keeps_included_off_component_off(self):
        rows=[{'id':name} for name in self.names]
        self.manager.config.update(enabled=['info-pane'],selected_modules=['info-pane'],
            included_components={'codex':['info-pane','context-drawer']},disabled=['context-drawer'])
        self.assertEqual(defaults(self.manager,rows,['context-drawer']),{'info-pane'})
        self.assertEqual(defaults(self.manager,rows,['statusline']),{'info-pane','statusline'})
        self.assertEqual(defaults(self.manager,rows,['context-drawer'],replace_selection=True),{'context-drawer'})
        self.manager.config.update(enabled=[],selected_modules=['info-pane'],included_components={'claude':['info-pane']})
        self.assertEqual(defaults(self.manager,rows,['statusline'],harness='codex'),{'statusline'})


class AliasTests(unittest.TestCase):
    def test_distribution_installs_the_three_aliases_from_one_package(self):
        import tomllib
        metadata=tomllib.loads((Path(__file__).resolve().parents[1]/'pyproject.toml').read_text())
        self.assertEqual(metadata['project']['scripts'],{'harness-ui':'codex_ui_mods.bootstrap:main','codex-ui':'codex_ui_mods.bootstrap:codex_main','claude-ui':'codex_ui_mods.bootstrap:claude_main'})

    def test_aliases_use_one_home_and_explicit_install_harness_wins(self):
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as folder,patch('codex_ui_mods.quick.prepare',return_value={}) as prepare_mock,patch('builtins.print'):
            for alias in ('codex','claude'):
                main(['--home',folder,'install','--dry-run'],default_harness=alias)
                self.assertEqual(prepare_mock.call_args.args[0].root,Path(folder))
                self.assertEqual(prepare_mock.call_args.args[1],alias)
            main(['--home',folder,'install','--harness','all','--dry-run'],default_harness='claude')
            self.assertEqual(prepare_mock.call_args.args[1],'all')

    def test_install_alias_harness_is_serialized_for_approved_source_reexec(self):
        from codex_ui_mods.cli import refreshed_manager
        with tempfile.TemporaryDirectory() as folder:
            manager=Manager(Path(folder))
            with patch.object(manager,'refresh'),patch('sys.argv',['claude-ui','install']),patch('codex_ui_mods.bootstrap.dispatch') as dispatch:
                refreshed_manager(manager,install_harness='claude')
            self.assertEqual(dispatch.call_args.kwargs['argv'],['install','--refreshed','--harness','claude'])

    def test_claude_alias_launches_native_claude_and_codex_alias_keeps_codex_launch(self):
        from codex_ui_mods.cli import main
        with tempfile.TemporaryDirectory() as folder:
            manager=Manager(Path(folder));manager.config['claude_install']={'command':'/fixture/native-claude'};manager.config['updates']='off';manager.save()
            with patch('os.execvpe') as execute,patch('os.execve',side_effect=AssertionError('unexpected Codex launch')),patch.object(Manager,'stock',side_effect=AssertionError('Claude launch inspected Codex')):
                self.assertEqual(main(['--home',folder,'launch','--','--resume','fixture'],default_harness='claude'),0)
                self.assertEqual(execute.call_args.args[:2],('/fixture/native-claude',['/fixture/native-claude','--resume','fixture']))
            with patch.object(Manager,'launch',return_value=('/fixture/codex',{})),patch.object(Manager,'stock',return_value={'version':'0.164.0'}),patch.object(Manager,'current',return_value={'binary':'/fixture/codex'}),patch('os.execve') as execute:
                self.assertEqual(main(['--home',folder,'launch','--','--resume','fixture'],default_harness='codex'),0)
                self.assertEqual(execute.call_args.args[:2],('/fixture/codex',['/fixture/codex','--resume','fixture']))
