import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.cli import main
from codex_ui_mods.core import Manager, atomic_json


class StatuslineCLITests(unittest.TestCase):
    def test_render_resolves_shared_root_and_passes_native_controls(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)/'home'
            settings={'state_root':str(Path(temporary)/'shared'),'environment':{}}
            output=io.StringIO()
            with patch('codex_ui_mods.surface.resolve_state',return_value=settings), \
                 patch('codex_ui_mods.claude_statusline.render',return_value='fixture line') as render, \
                 patch('sys.stdin',io.StringIO('{"session_id":"fixture"}')),patch('sys.stdout',output):
                self.assertEqual(main(['--home',str(home),'statusline','render','--provider','claude','--columns','80','--items','model,cache','--ascii','--no-color']),0)
            self.assertEqual(output.getvalue(),'fixture line\n')
            args=render.call_args.args
            self.assertEqual(args[2],80);self.assertEqual(args[3],settings['state_root'])
            self.assertEqual(args[4]['HARNESS_UI_STATUS_LINE_ITEMS'],'model,cache')
            self.assertEqual(args[4]['HARNESS_UI_STATUS_LINE_ASCII'],'1')
            self.assertEqual(args[4]['HARNESS_UI_STATUS_LINE_COLORS'],'0')

    def test_enable_and_features_all_delegate_native_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)/'home'; path=home/'catalog/adapters/claude-code/manifest.json'
            atomic_json(path,{'features':[{'id':'statusline','config_key':'statusline','default':False,'enable_all':True}]})
            with patch('codex_ui_mods.claude.configure_features',return_value={'verified':True}) as configure, \
                 patch('codex_ui_mods.claude_statusline.enable',return_value={'mode':'extend','changed':True}) as enable,patch('builtins.print'):
                self.assertEqual(main(['--home',str(home),'claude','statusline','enable','--mode','extend','--settings',str(home/'settings.json')]),0)
                self.assertEqual(enable.call_args.args[3],'extend')
                self.assertEqual(configure.call_args.args[0],{'statusline':True})
                self.assertEqual(main(['--home',str(home),'claude','features','all','--settings',str(home/'settings.json')]),0)
                self.assertEqual(configure.call_args.args[0],{'statusline':True})

    def test_merge_flag_is_forwarded_only_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary,patch('codex_ui_mods.surface.publish_session',return_value={'verified':True}) as publish,patch('builtins.print'):
            self.assertEqual(main(['--home',temporary,'state','publish-session','claude','fixture','--input-file',str(Path(temporary)/'input.json'),'--merge']),0)
            self.assertTrue(publish.call_args.kwargs['merge'])
