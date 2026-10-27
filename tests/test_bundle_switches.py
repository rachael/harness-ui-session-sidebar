"""Verified native supersets may change selection without downloading/rebuilding."""
import io
import json
import os
import platform
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from codex_ui_mods.core import Error, Manager, RUNTIME_SWITCHES, atomic_json, digest


class BundledSwitches(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = Manager(self.root/'state')
        self.stock = self.root/'stock'
        self.stock.write_text('#!/bin/sh\necho "codex-cli 0.163.0"\n')
        self.stock.chmod(0o755)
        self.manager.config['stock'] = str(self.stock)
        for name, flag in RUNTIME_SWITCHES.items():
            directory = self.manager.root/'catalog/modules'/name
            directory.mkdir(parents=True)
            (directory/'source.patch').write_text('synthetic '+name)
            atomic_json(directory/'module.json', {'schema': 1, 'id': name, 'version': '1',
                'description': 'Synthetic native module', 'codex': ['0.163.0'], 'order': 1,
                'patches': ['source.patch'], 'tests': [{'package': 'fixture', 'filter': name}],
                'dependencies': [], 'runtime_env': {flag: '1'}})
        self.compiled = {name: m['patch_hash'] for name, m in self.manager.modules().items()}
        self.manager.config['enabled'] = ['info-pane']
        self.stock_record = self.manager.stock()
        self.notices = {}
        for name in ('LICENSE', 'NOTICE'):
            target = self.root/name
            target.write_text('Synthetic upstream '+name)
            self.notices[name] = target

    def activate(self):
        return self.manager.activate(self.stock, self.stock_record,
            self.manager.selection('0.163.0'), 'artifact', 'https://example.invalid/bundle',
            'a'*40, self.notices, compiled_modules=self.compiled,
            runtime_switches=RUNTIME_SWITCHES)

    def test_switch_without_cargo_download_or_inheriting_disabled_flags(self):
        original = self.activate()
        self.manager.config['enabled'] = ['context-drawer']
        self.assertIsNone(self.manager.current())
        with patch.object(self.manager, 'build') as build, patch.object(self.manager, 'artifact') as download:
            with patch.dict(os.environ, {flag: '1' for flag in RUNTIME_SWITCHES.values()}):
                result = self.manager.update(refresh=False)
                binary, env = self.manager.launch([])
            build.assert_not_called()
            download.assert_not_called()
        self.assertEqual(result['status'], 'runtime-refreshed')
        self.assertEqual(digest(binary), original['sha256'])
        self.assertEqual(env['CODEX_UI_CONTEXT_DRAWER'], '1')
        self.assertEqual(env['CODEX_UI_INFO_PANE'], '0')
        self.assertEqual(env['CODEX_UI_STATUS_LINE'], '0')
        record = self.manager.current()
        source = json.loads((Path(record['binary']).parent/'source.json').read_text())
        self.assertEqual(source['modules'], self.compiled)
        self.assertEqual(source['runtime_switches'], RUNTIME_SWITCHES)

    def test_unknown_extras_changed_inactive_module_and_missing_switch_refuse(self):
        modules = self.manager.selection('0.163.0')
        check = self.manager.compiled_compatible
        self.assertFalse(check(self.compiled, {}, modules, '0.163.0'))
        self.assertFalse(check(dict(self.compiled, custom='b'*64), dict(RUNTIME_SWITCHES, custom='CODEX_UI_CUSTOM'), modules, '0.163.0'))
        self.assertFalse(check(self.compiled, {'info-pane': 'CODEX_UI_INFO_PANE'}, modules, '0.163.0'))
        self.activate()
        (self.manager.root/'catalog/modules/statusline/source.patch').write_text('changed')
        self.assertIsNone(self.manager.current(runtime=False))

    def test_smoke_uses_selected_flags_only_and_failure_preserves_current(self):
        original = self.activate()
        self.manager.config['enabled'] = ['context-drawer']
        modules = self.manager.selection('0.163.0')
        modules[0]['smoke'] = [['synthetic-smoke']]
        with patch.dict(os.environ, {flag: '1' for flag in RUNTIME_SWITCHES.values()}), patch('codex_ui_mods.core.run') as run:
            self.manager.smoke(Path(original['binary']), modules)
            env = run.call_args.kwargs['env']
            self.assertEqual(env['CODEX_UI_INFO_PANE'], '0')
            self.assertEqual(env['CODEX_UI_STATUS_LINE'], '0')
            self.assertEqual(env['CODEX_UI_CONTEXT_DRAWER'], '1')
        before = (self.manager.root/'current.json').read_bytes()
        with patch.object(self.manager, 'smoke', side_effect=Error('failed smoke')):
            with self.assertRaisesRegex(Error, 'failed smoke'):
                self.manager.update(refresh=False)
        self.assertEqual((self.manager.root/'current.json').read_bytes(), before)

    def test_persisted_statusline_options_revalidate_runtime_without_rebuild(self):
        self.manager.config['enabled'] = ['statusline']
        self.activate()
        self.manager.config['statusline_options'] = {'mode': 'extend', 'items': ['context', 'cache'], 'ascii': True, 'colors': False}
        self.assertIsNone(self.manager.current())
        with patch.object(self.manager, 'build') as build, patch.object(self.manager, 'artifact') as download:
            self.assertEqual(self.manager.update(refresh=False)['status'], 'runtime-refreshed')
            build.assert_not_called(); download.assert_not_called()
        _, env = self.manager.launch([])
        self.assertEqual(env['HARNESS_UI_STATUS_LINE_MODE'], 'extend')
        self.assertEqual(env['HARNESS_UI_STATUS_LINE_ITEMS'], 'context,cache')
        self.assertEqual(env['HARNESS_UI_STATUS_LINE_ASCII'], '1')
        self.assertEqual(env['HARNESS_UI_STATUS_LINE_COLORS'], '0')
        modules = self.manager.selection('0.163.0')
        modules[0]['smoke'] = [['fake-smoke']]
        with patch('codex_ui_mods.core.run') as run:
            self.manager.smoke(self.stock, modules)
            self.assertEqual(run.call_args.kwargs['env']['HARNESS_UI_STATUS_LINE_MODE'], 'extend')
        self.manager.config['statusline_options']['mode'] = 'invalid'
        self.assertIsNone(self.manager.current())
        self.assertEqual(self.manager.launch([])[0], str(self.stock.resolve()))
        self.manager.config['enabled'] = ['info-pane']
        self.assertEqual(self.manager.statusline_environment(self.manager.selection('0.163.0')), {})

    def test_downloaded_superset_metadata_must_match_catalog(self):
        metadata = {'schema': 1, 'codex': '0.163.0', 'upstream_commit': 'a'*40,
                    'modules': self.compiled, 'runtime_switches': RUNTIME_SWITCHES,
                    'binary_sha256': digest(self.stock)}
        def archive(meta):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:gz') as tar:
                for name, data in {'codex': self.stock.read_bytes(), 'source.json': json.dumps(meta).encode(),
                                  'upstream/LICENSE': b'License', 'upstream/NOTICE': b'Notice'}.items():
                    info = tarfile.TarInfo(name); info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            return output.getvalue()
        def install(data):
            import hashlib
            bundle = dict(metadata, platform=platform.system().lower()+'-'+platform.machine().lower(),
                          url='https://example.invalid/bundle', sha256=hashlib.sha256(data).hexdigest())
            atomic_json(self.manager.root/'catalog/catalog.json', {'bundles': [bundle]})
            response = MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = bundle['url']
            response.read.side_effect = io.BytesIO(data).read
            with patch('codex_ui_mods.core.urllib.request.urlopen', return_value=response):
                return self.manager.artifact(self.stock_record, self.manager.selection('0.163.0'))
        with self.assertRaisesRegex(Error, 'source metadata'):
            install(archive(dict(metadata, runtime_switches={})))
        self.assertFalse((self.manager.root/'current.json').exists())
        self.assertEqual(install(archive(metadata))['compiled_modules'], self.compiled)


if __name__ == '__main__':
    unittest.main()
