"""Two real local Git versions: approval, refresh, subsequent entry/hook dispatch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from codex_ui_mods.bootstrap import RECEIPT, approve, approved_source, home_path
from codex_ui_mods.core import Manager

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.installed=self.base/'installed';self.remote=self.base/'remote'
        shutil.copytree(ROOT/'src',self.installed/'src',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'src',self.remote/'src',ignore=shutil.ignore_patterns('__pycache__'))
        (self.remote/'.gitignore').write_text('__pycache__/\n*.pyc\n')
        package=self.remote/'src/codex_ui_mods'
        (package/'controller_impl.py').write_bytes((package/'cli.py').read_bytes())
        (package/'cli.py').write_text('import sys,json\nfrom .controller_impl import main as delegate\ndef main(argv=None,default_harness=None):\n print("MANAGED_V2",file=sys.stderr)\n if "v2-marker" in (sys.argv[1:] if argv is None else argv):\n  print(json.dumps({"controller":"v2"}));return 0\n return delegate(argv,default_harness)\n')
        (package/'quick.py').write_text('def prepare(manager,harness=None,*args,**kwargs):return {"controller":"v2","target":harness}\n')
        codex=package/'codex.py';codex.write_text('from pathlib import Path\nimport os\nPath(os.environ["PROOF_HOOK_MARKER"]).write_text("v2")\n'+codex.read_text())
        for arguments in (('init','-b','main'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid'),('add','.'),('commit','-qm','Controller v2 fixture')):self.git(*arguments)
        self.bin=self.base/'bin';self.bin.mkdir()
        for alias,entry in (('harness-ui','main'),('codex-ui','codex_main'),('claude-ui','claude_main')):
            script=self.bin/alias;script.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0,{str(self.installed/"src")!r})\nfrom codex_ui_mods.bootstrap import {entry}\nraise SystemExit({entry}())\n');script.chmod(0o755)
        stock=self.bin/'stock';stock.write_text('#!/bin/sh\nif [ "$1" = --version ]; then echo codex-cli 0.164.0; else echo STOCK "$@"; echo PY_PATH="$PYTHONPATH"; fi\n');stock.chmod(0o755)
        self.home=self.base/'manager';self.manager=Manager(self.home)
        self.manager.config.update(catalog_remote=str(self.remote),catalog_branch='main',updates='off',stock=str(stock),metadata_url='http://127.0.0.1:9/catalog.json');self.manager.save()
        self.env=dict(os.environ,HOME=str(self.base/'user'),PYTHONPATH=str(self.installed/'src'),HARNESS_UI_HOME=str(self.home),PROOF_HOOK_MARKER=str(self.base/'hook-marker'))
        for key in ('CODEX_UI_HOME','HARNESS_UI_STATE_DIR','CODEX_UI_STATE_DIR','HARNESS_UI_DISPATCHED'):self.env.pop(key,None)

    def git(self,*arguments):
        return subprocess.check_output(['git','-C',str(self.remote),*arguments],stderr=subprocess.STDOUT,text=True).strip()

    def command(self,alias,*arguments,input=None,cwd=None):
        return subprocess.run([str(self.bin/alias),'--home',str(self.home),*arguments],env=self.env,input=input,capture_output=True,text=True,timeout=30,cwd=cwd)

    def test_real_alias_refresh_executes_v2_and_preserves_harness_default(self):
        for alias,harness in (('codex-ui','codex'),('claude-ui','claude')):
            if (self.home/RECEIPT).exists():(self.home/RECEIPT).unlink()
            result=self.command(alias,'install','--non-interactive')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'controller':'v2','target':harness})
            self.assertIsNotNone(approved_source(self.home))
        result=self.command('claude-ui','install','--harness','all','--non-interactive')
        self.assertEqual(json.loads(result.stdout)['target'],'all')

    def test_approved_update_persists_for_new_parser_launch_and_installed_hook(self):
        updated=self.command('harness-ui','update','--yes')
        self.assertEqual(updated.returncode,0,updated.stderr);self.assertIn('MANAGED_V2',updated.stderr)
        installed_core=self.installed/'src/codex_ui_mods/core.py'
        installed_core.write_text('raise RuntimeError("old core imported before dispatch")\n')
        result=self.command('harness-ui','v2-marker')
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),{'controller':'v2'})
        # No implicit fetch: even a vanished remote cannot affect local dispatch.
        shutil.move(self.remote,self.base/'remote-unavailable')
        for policy in ('off','ask'):
            self.manager.config['updates']=policy;self.manager.save()
            launched=self.command('harness-ui','launch','--','fixture')
            self.assertEqual(launched.returncode,0,launched.stderr);self.assertIn('MANAGED_V2',launched.stderr)
            self.assertIn('STOCK fixture',launched.stdout)
            self.assertIn('PY_PATH='+self.env['PYTHONPATH'],launched.stdout)
            self.assertNotIn(str(self.home/'catalog/src'),launched.stdout)
        hook=subprocess.run([sys.executable,'-m','codex_ui_mods.codex','hook'],env=self.env,input='{"session_id":"fixture","hook_event_name":"Stop"}',capture_output=True,text=True,timeout=20,cwd=self.base)
        self.assertEqual(hook.returncode,0,hook.stderr);self.assertEqual((self.base/'hook-marker').read_text(),'v2')
        # An installed package in CWD cannot shadow the approved source or loop.
        result=self.command('harness-ui','v2-marker',cwd=self.installed/'src')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_actual_native_launch_children_inherit_selected_home_for_approved_hook(self):
        updated=self.command('harness-ui','update','--yes')
        self.assertEqual(updated.returncode,0,updated.stderr)
        native=self.bin/'native-child'
        native.write_text(f'''#!{sys.executable}
import json,os,subprocess,sys
if sys.argv[1:]==['--version']:
 print('codex-cli 0.164.0')
else:
 child=subprocess.run([sys.executable,'-P','-m','codex_ui_mods.codex','hook'],input='{{"session_id":"fixture","hook_event_name":"Stop"}}',capture_output=True,text=True)
 print(json.dumps({{'home':os.environ.get('HARNESS_UI_HOME'),'legacy_home':os.environ.get('CODEX_UI_HOME'),'pythonpath':os.environ.get('PYTHONPATH'),'hook_exit':child.returncode}}))
 if child.returncode:print(child.stderr,file=sys.stderr)
 raise SystemExit(child.returncode)
''')
        native.chmod(0o755)
        self.manager.config.update(stock=str(native),claude_install={'command':str(native)})
        self.manager.save()
        (self.installed/'src/codex_ui_mods/core.py').write_text('raise RuntimeError("stale installed core reached")\n')
        # Only --home supplies the manager location to the outer installed entry.
        self.env.pop('HARNESS_UI_HOME',None);self.env.pop('CODEX_UI_HOME',None)
        for alias in ('harness-ui','claude-ui'):
            marker=self.base/'hook-marker'
            if marker.exists():marker.unlink()
            launched=self.command(alias,'launch','--','fixture')
            self.assertEqual(launched.returncode,0,launched.stderr)
            state=json.loads(launched.stdout)
            self.assertEqual(state['home'],str(self.home.resolve()))
            self.assertEqual(state['legacy_home'],str(self.home.resolve()))
            self.assertEqual(state['pythonpath'],self.env['PYTHONPATH'])
            self.assertEqual(state['hook_exit'],0)
            self.assertEqual(marker.read_text(),'v2')

    def test_dirty_unapproved_and_mismatched_sources_fall_back_without_executing_v2(self):
        commit=self.manager.refresh()
        receipt=(self.home/RECEIPT).read_text();target=self.home/'catalog/src/codex_ui_mods/cli.py'
        def refused():
            result=self.command('harness-ui','v2-marker');self.assertEqual(result.returncode,2,result.stderr);self.assertNotIn('MANAGED_V2',result.stderr)
        (self.home/RECEIPT).unlink();refused();(self.home/RECEIPT).write_text(receipt)
        content=target.read_text();target.write_text(content+'\n# local edit\n');refused();target.write_text(content)
        (self.home/'catalog/unknown.py').write_text('raise RuntimeError("unapproved")');refused();(self.home/'catalog/unknown.py').unlink()
        record=json.loads(receipt);record['commit']='0'*40;(self.home/RECEIPT).write_text(json.dumps(record));refused();(self.home/RECEIPT).write_text(receipt)
        record=json.loads(receipt);record['schema']=True;(self.home/RECEIPT).write_text(json.dumps(record));refused();(self.home/RECEIPT).write_text(receipt)
        subprocess.run(['git','-C',str(self.home/'catalog'),'remote','set-url','origin','https://example.invalid/other'],check=True)
        refused()
        subprocess.run(['git','-C',str(self.home/'catalog'),'remote','set-url','origin',str(self.remote)],check=True)
        subprocess.run(['git','-C',str(self.home/'catalog'),'checkout','-qb','unapproved-branch'],check=True)
        refused()
        subprocess.run(['git','-C',str(self.home/'catalog'),'checkout','-q','main'],check=True)
        self.manager.config['catalog_remote']='https://example.invalid/changed';self.manager.save();refused()
        self.assertEqual(self.git('rev-parse','HEAD'),commit)

    def test_home_resolution_uses_global_override_and_does_not_consume_native_arguments(self):
        self.assertEqual(home_path(['--home',str(self.home),'launch','--','--home','native']),self.home)
        self.assertEqual(home_path(['--home='+str(self.home),'v2-command']),self.home)
        self.assertEqual(home_path(['--home','','v2-command']),Path('.'))
