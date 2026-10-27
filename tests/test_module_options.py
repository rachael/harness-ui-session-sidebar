import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Error, Manager, atomic_json, manifest


class ModuleOptions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.m = Manager(self.root/'state')
        stock = self.root/'stock'
        stock.write_text('#!/bin/sh\necho "codex-cli 0.163.0"\n'); stock.chmod(0o755)
        self.m.config['stock'] = str(stock)
        self.file = self.m.root/'modules/fixture/module.json'
        self.data = {'schema':1, 'id':'fixture', 'version':'1', 'description':'Fixture', 'codex':['*'],
                     'order':1, 'dependencies':[], 'patches':[], 'tests':[], 'runtime_env':{},
                     'options':[
                         {'id':'switch_tabs','type':'boolean','default':True,'runtime_env':'HARNESS_UI_SWITCH_TABS'},
                         {'id':'layout','type':'choice','default':'compact','choices':['compact','wide'],'runtime_env':'HARNESS_UI_LAYOUT'}]}
        atomic_json(self.file, self.data)
        self.m.config['enabled'] = ['fixture']

    def test_default_and_configured_options_apply_only_to_selected_modules(self):
        self.m.update(refresh=False)
        self.assertEqual(self.m.launch([])[1]['HARNESS_UI_SWITCH_TABS'], '1')
        self.m.config['module_options'] = {'fixture': {'switch_tabs':False, 'layout':'wide'}}
        self.assertIsNone(self.m.current())
        self.m.update(refresh=False)
        env = self.m.launch([])[1]
        self.assertEqual(env['HARNESS_UI_SWITCH_TABS'], '0')
        self.assertEqual(env['HARNESS_UI_LAYOUT'], 'wide')
        self.m.config['enabled'] = []
        with patch.dict(os.environ, HARNESS_UI_SWITCH_TABS='1'):
            self.assertNotIn('HARNESS_UI_SWITCH_TABS', self.m.launch([])[1])

    def test_invalid_manifest_and_config_never_execute(self):
        for options in ([{'id':'x','type':'boolean','default':'yes'}],
                        [{'id':'x','type':'choice','choices':['one'],'default':'two'}],
                        [{'id':'x','type':'boolean','default':True,'runtime_env':'PATH'}],
                        [{'id':'x','type':'boolean','default':True,'config_key':'settings.command'}]):
            atomic_json(self.file, dict(self.data, options=options))
            with self.assertRaises(Error): manifest(self.file)
        atomic_json(self.file, self.data)
        for value in ({'switch_tabs':'false'}, {'layout':'unknown'}, {'undeclared':True}):
            self.m.config['module_options'] = {'fixture':value}
            with self.assertRaises(Error): self.m.update(refresh=False)
            self.assertFalse((self.m.root/'current.json').exists())

    def test_smoke_and_launch_share_option_values(self):
        self.m.config['module_options'] = {'fixture':{'switch_tabs':False}}
        chosen = self.m.selection('0.163.0')
        chosen[0]['smoke'] = [['synthetic']]
        with patch('codex_ui_mods.core.run') as run:
            self.m.smoke(self.root/'stock', chosen)
            self.assertEqual(run.call_args.kwargs['env']['HARNESS_UI_SWITCH_TABS'], '0')
            self.assertEqual(run.call_args.kwargs['env']['HARNESS_UI_LAYOUT'], 'compact')

    def test_inactive_options_are_scrubbed_in_smoke_and_error_fallback(self):
        self.m.update(refresh=False)
        other = dict(self.data, id='other', options=[], runtime_env={}, smoke=[['synthetic']])
        atomic_json(self.m.root/'modules/other/module.json', other)
        selected = [self.m.modules()['other']]
        with patch.dict(os.environ, HARNESS_UI_SWITCH_TABS='1', HARNESS_UI_LAYOUT='wide'):
            with patch('codex_ui_mods.core.run') as run:
                self.m.smoke(self.root/'stock', selected)
                self.assertNotIn('HARNESS_UI_SWITCH_TABS', run.call_args.kwargs['env'])
                self.assertNotIn('HARNESS_UI_LAYOUT', run.call_args.kwargs['env'])
            current = self.m.current()
            with patch.object(self.m, 'current', return_value=current), \
                    patch.object(self.m, 'module_options_environment', side_effect=Error('changed options')):
                binary, environment = self.m.launch([])
                self.assertEqual(Path(binary).resolve(), (self.root/'stock').resolve())
                self.assertNotIn('HARNESS_UI_SWITCH_TABS', environment)
                self.assertNotIn('HARNESS_UI_LAYOUT', environment)


if __name__ == '__main__': unittest.main()
