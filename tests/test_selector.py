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

from codex_ui_mods.core import Error
from codex_ui_mods.selector import choose, option_definitions, parse_options

ROWS = [{'id':'sidebar','label':'Sidebar','options':[
    {'id':'switch_tabs','type':'boolean','default':True,'runtime_env':'HARNESS_UI_SIDEBAR_SWITCH_TABS'},
    {'id':'switch_panes','type':'boolean','default':True},
    {'id':'placement','type':'choice','choices':['left','right'],'default':'left'}]}]


class SelectorTests(unittest.TestCase):
    def test_enter_accepts_defaults_from_top_without_other_questions(self):
        output = io.StringIO()
        result = choose(ROWS, {'sidebar'}, key_reader=lambda:'accept', output=output)
        self.assertEqual(result['modules'], ['sidebar'])
        self.assertTrue(result['options']['sidebar']['switch_tabs'])
        self.assertFalse(result['changed'])
        self.assertIn('› [ Enter ] Accept and continue', output.getvalue().splitlines()[0])

    def test_keyboard_module_and_expanded_typed_options(self):
        keys = iter(['down','space','right','down','space','down','down','space','accept'])
        result = choose(ROWS, set(), key_reader=lambda:next(keys), output=io.StringIO())
        self.assertEqual(result['modules'], ['sidebar'])
        self.assertFalse(result['options']['sidebar']['switch_tabs'])
        self.assertTrue(result['options']['sidebar']['switch_panes'])
        self.assertEqual(result['options']['sidebar']['placement'], 'right')
        self.assertTrue(result['changed'])

    def test_declared_options_only_and_headless_values(self):
        self.assertEqual(parse_options(ROWS,['sidebar.switch_tabs=false'])['sidebar']['switch_tabs'],False)
        for assignment in ('sidebar.unknown=false','sidebar.switch_tabs=string','sidebar.placement=elsewhere'):
            with self.assertRaises(Error): parse_options(ROWS,[assignment])
        with self.assertRaises(Error): option_definitions({'options':[{'id':'bad','type':'boolean','default':'true'}]})
        with self.assertRaises(Error): option_definitions({'options':[{'id':'bad','type':'boolean','default':True,'runtime_env':'PATH'}]})

    def test_real_terminal_enter_accepts_once(self):
        master, slave = pty.openpty()
        script = 'import json,sys;from codex_ui_mods.selector import terminal,choose;rows='+repr(ROWS)+'\nwith terminal(sys.stdin.fileno()): result=choose(rows,{"sidebar"})\nprint("ACCEPTED",json.dumps(result))\n'
        child = subprocess.Popen([sys.executable,'-c',script],stdin=slave,stdout=slave,stderr=slave)
        os.close(slave)
        raw = b''
        try:
            deadline = time.monotonic()+5
            sent = False
            while time.monotonic()<deadline:
                if select.select([master],[],[],.1)[0]:
                    try: chunk=os.read(master,65536)
                    except OSError: break
                    raw += chunk
                    if b'Accept and continue' in raw and not sent:
                        os.write(master,b'\r'); sent=True
                if child.poll() is not None: break
            child.wait(timeout=2)
            self.assertEqual(child.returncode,0,raw)
            self.assertIn(b'ACCEPTED',raw)
            self.assertIn(b'"modules": ["sidebar"]',raw)
        finally:
            if child.poll() is None: child.kill();child.wait()
            os.close(master)

    def test_actual_cli_one_step_accept_preserves_scoped_preselection(self):
        from codex_ui_mods.core import atomic_json
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)/'home'
            for name in ('sidebar','other'):
                folder=home/'catalog/modules'/name
                atomic_json(folder/'module.json',{'schema':1,'id':name,'version':'1','description':'Fixture',
                            'codex':['*'],'order':1,'dependencies':[],'patches':[],'tests':[],
                            'options':ROWS[0]['options'] if name=='sidebar' else []})
            script='from unittest.mock import patch;from codex_ui_mods.cli import main\nwith patch("codex_ui_mods.quick.prepare",return_value={"ACCEPTED":True}) as prepare:\n main('+repr(['--home',str(home),'install','--no-refresh','--harness','codex','--module','sidebar','--option','sidebar.switch_tabs=false'])+')\n print("SCOPED",prepare.call_args.args[6])\n print("CHOICE_FLAG",prepare.call_args.args[10]["sidebar"]["switch_tabs"])\n'
            master,slave=pty.openpty()
            child=subprocess.Popen([sys.executable,'-c',script],stdin=slave,stdout=slave,stderr=slave)
            os.close(slave);raw=b''
            try:
                deadline=time.monotonic()+5;sent=False
                while time.monotonic()<deadline:
                    if select.select([master],[],[],.1)[0]:
                        try:raw+=os.read(master,65536)
                        except OSError:break
                        if b'Accept and continue' in raw and not sent:os.write(master,b'\r');sent=True
                    if child.poll() is not None:break
                child.wait(timeout=2)
                self.assertEqual(child.returncode,0,raw)
                self.assertIn(b'[x] sidebar',raw);self.assertIn(b'[ ] other',raw)
                self.assertIn(b"SCOPED ['sidebar']",raw)
                self.assertIn(b'CHOICE_FLAG False',raw)
            finally:
                if child.poll() is None:child.kill();child.wait()
                os.close(master)
