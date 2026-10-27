import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_ui_mods.claude import install
from codex_ui_mods.core import Error,Manager,atomic_json


class NativeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.user=self.root/'user';self.user.mkdir()
        self.registry=self.root/'native-list.json'
        self.log=self.root/'commands.jsonl'
        self.preferences=self.root/'preferences.json'
        self.native=self.root/'cache/info-pane';self.native.mkdir(parents=True)
        (self.native/'keep.txt').write_text('native package')
        self.source=self.root/'source'
        atomic_json(self.source/'.claude-plugin/plugin.json',{'name':'info-pane','version':'1'})
        self.command=self.root/'claude'
        self.command.write_text(f'#!{sys.executable}\nimport json,pathlib,sys\n'
            f'with pathlib.Path({str(self.log)!r}).open("a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'if sys.argv[1:]==["--version"]:print("2.1.310 (Claude Code)")\n'
            f'elif sys.argv[1:]==["plugin","list","--json"]:print(pathlib.Path({str(self.registry)!r}).read_text())\n'
            f'elif sys.argv[1:]==["plugin","configure","info-pane@harness-ui-mods","--json"]:print(pathlib.Path({str(self.preferences)!r}).read_text())\n'
            f'elif sys.argv[1:]==["plugin","configure","info-pane@harness-ui-mods","--values-stdin"]:\n p=pathlib.Path({str(self.preferences)!r});d=json.loads(p.read_text());d.setdefault("inputs",{{}}).update(json.load(sys.stdin));p.write_text(json.dumps(d));print("{{}}")\n'
            'elif sys.argv[1:3]==["plugin","validate"]:print("{}")\n'
            f'elif sys.argv[1:3]==["plugin","install"]:pathlib.Path({str(self.registry)!r}).write_text(json.dumps([{{"id":"info-pane@harness-ui-mods","installPath":{str(self.native)!r},"enabled":True}}]));print("{{}}")\n'
            'else:raise SystemExit(9)\n')
        self.command.chmod(0o755)
        env=patch.dict(os.environ,{'HOME':str(self.user),'HARNESS_UI_HOME':str(self.root/'home')},clear=False)
        env.start();self.addCleanup(env.stop)

    def entry(self, enabled):
        return {'id':'info-pane@harness-ui-mods','installPath':str(self.native),'enabled':enabled,'scope':'user','version':'1'}

    def test_native_enabled_and_disabled_never_create_managed_copy_or_config(self):
        for enabled in (True,False):
            atomic_json(self.registry,[self.entry(enabled)])
            destination=self.user/'.claude/skills/info-pane'
            result=install(self.source,destination,str(self.command))
            self.assertEqual(result['ownership'],'native');self.assertEqual(result['enabled'],enabled)
            self.assertEqual(result['plugin_ref'],'info-pane@harness-ui-mods')
            self.assertFalse(destination.exists())
            self.assertEqual((self.native/'keep.txt').read_text(),'native package')
        calls=[json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(all(call==['--version'] or call==['plugin','list','--json'] for call in calls))

    def test_explicit_managed_collision_refuses_before_copy_and_dryrun_is_readonly(self):
        atomic_json(self.registry,[self.entry(True)])
        destination=self.user/'.claude/skills/info-pane'
        with self.assertRaisesRegex(Error,'deprecated'):
            install(self.source,destination,str(self.command),ownership='managed')
        result=install(self.root/'not-downloaded',destination,str(self.command),dry_run=True)
        self.assertTrue(result['dry_run']);self.assertEqual(result['ownership'],'native')
        self.assertFalse(destination.parent.exists())

    def test_native_removed_update_never_falls_back_to_cached_managed_copy(self):
        atomic_json(self.registry,[])
        manager=Manager(self.root/'managed')
        manager.config.update(stock=str(self.root/'missing-codex'),claude_install={
            'destination':str(self.native),'source':str(self.source),'command':str(self.command),
            'ownership':'native','plugin':'info-pane@harness-ui-mods'})
        with self.assertRaisesRegex(Error,'no native info-pane'):
            manager.update(refresh=False)
        self.assertFalse((self.native/'.harness-ui-install.json').exists())
        self.assertEqual((self.native/'keep.txt').read_text(),'native package')

    def test_unchanged_interactive_enter_preserves_disabled_native_choices(self):
        atomic_json(self.registry,[self.entry(False)])
        home=self.root/'interactive-home'
        atomic_json(home/'catalog/adapters/claude-code/manifest.json',{'features':[
            {'id':'info-pane','config_key':'infoPane','default':True},
            {'id':'context-drawer','config_key':'contextDrawer','default':True},
            {'id':'statusline','config_key':'statusline','default':False}]})
        script='from codex_ui_mods.cli import main\nraise SystemExit(main('+repr([
            '--home',str(home),'install','--no-refresh','--harness','claude','--claude-command',str(self.command)])+'))\n'
        master,slave=pty.openpty()
        child=subprocess.Popen([sys.executable,'-c',script],stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ))
        os.close(slave);raw=b''
        try:
            sent=False;deadline=time.monotonic()+7
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try:raw+=os.read(master,65536)
                    except OSError:break
                    if b'Accept and continue' in raw and not sent:os.write(master,b'\r');sent=True
                if child.poll() is not None:break
            child.wait(timeout=2)
            self.assertEqual(child.returncode,0,raw)
            self.assertIn(b'"ownership": "native"',raw)
            self.assertFalse((self.user/'.claude/skills/info-pane').exists())
            self.assertFalse((self.user/'.claude/settings.json').exists())
            self.assertFalse((home/'claude-statusline.json').exists())
            calls=[json.loads(line) for line in self.log.read_text().splitlines()]
            self.assertTrue(all(call==['--version'] or call==['plugin','list','--json'] for call in calls),calls)
        finally:
            if child.poll() is None:child.kill();child.wait()
            os.close(master)

    def test_enter_seeds_native_saved_false_and_never_writes_passive_defaults(self):
        atomic_json(self.registry,[self.entry(True)])
        atomic_json(self.preferences,{'schema':{'sessionSidebar':{'default':False},'switchTabs':{'default':True}},
                                     'inputs':{'sessionSidebar':'true','switchTabs':'false'}})
        home=self.root/'saved-native-home'
        atomic_json(home/'catalog/adapters/claude-code/manifest.json',{'features':[
            {'id':'session-sidebar','config_key':'sessionSidebar','default':False,'options':[
                {'id':'switch_tabs','label':'Switch tabs','type':'boolean','default':True,'config_key':'switchTabs'}]}]})
        manager=Manager(home);manager.config.update(installed_harnesses=['claude'],selected_modules=['session-sidebar']);manager.save()
        script='from codex_ui_mods.cli import main\nraise SystemExit(main('+repr([
            '--home',str(home),'install','--no-refresh','--harness','claude','--claude-command',str(self.command)])+'))\n'
        master,slave=pty.openpty();child=subprocess.Popen([sys.executable,'-c',script],stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ));os.close(slave)
        raw=b'';sent=False;accepted=False;deadline=time.monotonic()+7
        try:
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try:raw+=os.read(master,65536)
                    except OSError:break
                    if b'Accept and continue' in raw and not sent:os.write(master,b'\x1b[B\x1b[C');sent=True
                    if b'Switch tabs: off' in raw and not accepted:os.write(master,b'\r');accepted=True
                if child.poll() is not None:break
            child.wait(timeout=2)
            self.assertEqual(child.returncode,0,raw);self.assertTrue(accepted,raw)
            self.assertFalse((self.user/'.claude/settings.json').exists())
            calls=[json.loads(line) for line in self.log.read_text().splitlines()]
            self.assertFalse(any('--values-stdin' in call for call in calls),calls)
            self.assertEqual(json.loads(self.preferences.read_text())['inputs']['switchTabs'],'false')
        finally:
            if child.poll() is None:child.kill();child.wait()
            os.close(master)

    def test_explicit_option_updates_the_native_saved_value(self):
        from codex_ui_mods.cli import main
        atomic_json(self.registry,[self.entry(True)])
        atomic_json(self.preferences,{'schema':{'sessionSidebar':{},'switchTabs':{}},'inputs':{'sessionSidebar':'true','switchTabs':'false'}})
        home=self.root/'explicit-home'
        atomic_json(home/'catalog/adapters/claude-code/manifest.json',{'features':[
            {'id':'session-sidebar','config_key':'sessionSidebar','default':False,'options':[
                {'id':'switch_tabs','type':'boolean','default':True,'config_key':'switchTabs'}]}]})
        with patch('builtins.print'):
            self.assertEqual(main(['--home',str(home),'install','--no-refresh','--non-interactive','--harness','claude','--claude-command',str(self.command),'--option','session-sidebar.switch_tabs=true']),0)
        self.assertEqual(json.loads(self.preferences.read_text())['inputs']['switchTabs'],'true')

    def test_fresh_native_enter_preserves_disabled_statusline_and_sidebar(self):
        atomic_json(self.registry,[])
        atomic_json(self.preferences,{'schema':{'statusline':{},'sessionSidebar':{}},'inputs':{'statusline':'false','sessionSidebar':'false'}})
        home=self.root/'disabled-fresh-home'
        atomic_json(home/'catalog/.claude-plugin/marketplace.json',{'name':'harness-ui-mods','plugins':[]})
        atomic_json(home/'catalog/adapters/claude-code/.claude-plugin/plugin.json',{'name':'info-pane','version':'1'})
        atomic_json(home/'catalog/adapters/claude-code/manifest.json',{'features':[
            {'id':'statusline','config_key':'statusline','default':False},
            {'id':'session-sidebar','config_key':'sessionSidebar','default':False}]})
        manager=Manager(home);manager.config['disabled']=['statusline','session-sidebar'];manager.save()
        script='from codex_ui_mods.cli import main\nraise SystemExit(main('+repr([
            '--home',str(home),'install','--no-refresh','--harness','claude','--claude-command',str(self.command)])+'))\n'
        master,slave=pty.openpty();child=subprocess.Popen([sys.executable,'-c',script],stdin=slave,stdout=slave,stderr=slave,env=dict(os.environ));os.close(slave);raw=b'';sent=False;deadline=time.monotonic()+7
        try:
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try:raw+=os.read(master,65536)
                    except OSError:break
                    if b'Accept and continue' in raw and not sent:os.write(master,b'\r');sent=True
                if child.poll() is not None:break
            child.wait(timeout=2);self.assertEqual(child.returncode,0,raw)
            self.assertEqual(json.loads(self.preferences.read_text())['inputs'],{'statusline':'false','sessionSidebar':'false'})
            self.assertFalse((self.user/'.claude/settings.json').exists())
            self.assertFalse((home/'claude-statusline.json').exists())
        finally:
            if child.poll() is None:child.kill();child.wait()
            os.close(master)

    def test_malformed_listings_refuse_dryrun_before_install_or_config(self):
        good=self.entry(True)
        malformed=[None, 7, 'entry', {}, dict(good,id=7), dict(good,id='info-pane'),
                   dict(good,id='info-pane@'),dict(good,enabled='false'),
                   dict(good,enabled=1),dict(good,projectEnabled=None),
                   dict(good,installPath=None),dict(good,installPath=[]),
                   dict(good,installPath='relative/cache'),dict(good,installPath='/cache\x00bad')]
        for bad in malformed:
            with self.subTest(entry=bad):
                atomic_json(self.registry,[bad])
                with self.assertRaisesRegex(Error,'malformed'):
                    install(self.source,None,str(self.command),dry_run=True)
        self.registry.write_text('{invalid')
        with self.assertRaisesRegex(Error,'invalid JSON'):
            install(self.source,None,str(self.command),dry_run=True)
        calls=[json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(all(call==['--version'] or call==['plugin','list','--json'] for call in calls))

    def test_native_cli_entry_shapes_accept_unrelated_and_optional_boolean_flags(self):
        from codex_ui_mods.claude import native_ownership
        # Native marketplace and skills-directory shapes from the CLI, with
        # synthetic names/paths; unrelated plugin payloads are left untouched.
        unrelated={'id':'fixture@official','version':'abc123','scope':'user','enabled':True,
                   'installPath':str(self.root/'cache/fixture'),'projectEnabled':False,
                   'installedAt':'2026-10-26T00:00:00Z','mcpServers':{'fixture':{'type':'http'}}}
        for flags in ({'enabled':True},{'projectEnabled':True},{'enabled':False,'projectEnabled':False},{}):
            entry={'id':'info-pane@harness-ui-mods','installPath':str(self.native),**flags}
            atomic_json(self.registry,[unrelated,entry])
            selected,listing=native_ownership(str(self.command))
            self.assertEqual(selected,entry);self.assertEqual(listing[0],unrelated)
        atomic_json(self.registry,[unrelated])
        self.assertIsNone(native_ownership(str(self.command))[0])

    def test_legacy_enabled_blocks_native_duplicate_and_disabled_is_readonly(self):
        legacy={'id':'info-pane@skills-dir','installPath':str(self.user/'.claude/skills/info-pane'),'enabled':True,'scope':'user'}
        atomic_json(self.registry,[legacy])
        with self.assertRaisesRegex(Error,'legacy .* is loaded'):
            install(self.source,None,str(self.command))
        probe=install(self.source,None,str(self.command),dry_run=True)
        self.assertEqual(probe['ownership'],'legacy');self.assertTrue(probe['enabled']);self.assertTrue(probe['dry_run'])
        atomic_json(self.registry,[legacy,self.entry(True)])
        with self.assertRaisesRegex(Error,'both enabled'):
            install(self.source,None,str(self.command),dry_run=True)
        legacy['enabled']=False;atomic_json(self.registry,[legacy])
        result=install(self.source,None,str(self.command))
        self.assertEqual(result['ownership'],'legacy');self.assertFalse(result['enabled'])
        self.assertFalse((self.user/'.claude/skills/info-pane').exists())
        calls=[json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertFalse(any(call[:2]==['plugin','install'] for call in calls))
