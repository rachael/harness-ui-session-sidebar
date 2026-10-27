import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from codex_ui_mods.core import manifest
from codex_ui_mods.selector import option_definitions

ROOT=Path(__file__).resolve().parents[1]


class TemplateTests(unittest.TestCase):
    def test_starter_is_unselected_data_module_with_supported_options(self):
        value=manifest(ROOT/'templates/module/module.json')
        self.assertEqual(value['patches'],[])
        self.assertEqual(value['dependencies'],[])
        self.assertEqual(option_definitions(value)[0]['type'],'boolean')

    def test_real_startup_smoke_submits_no_turn_and_accepts_typed_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate=Path(directory)/'codex'
            candidate.write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 8\necho codex-cli 0.164.0\n');candidate.chmod(0o755)
            result=subprocess.run([sys.executable,str(ROOT/'templates/module/smoke.py'),str(candidate)],
                                  env=dict(os.environ,HARNESS_UI_EXAMPLE_FLAG='0'),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('option=0',result.stdout)
