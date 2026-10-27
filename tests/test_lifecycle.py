import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods import cli
from codex_ui_mods.core import Error, Manager, atomic_json, discover, manifest, select, run


def module(root, name='fixture', **fields):
    path = root/name; path.mkdir(parents=True)
    m = {'schema': 1, 'id': name, 'version': '1', 'description': 'Synthetic fixture', 'codex': ['1.2.3'],
         'order': 1, 'dependencies': [], 'patches': [], 'tests': [], 'smoke': [], 'runtime_env': {}}
    m.update(fields); atomic_json(path/'module.json', m)
    return path


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.m = Manager(self.base/'state')
        self.stock = self.base/'stock'
        self.stock.write_text('#!/bin/sh\necho "codex-cli 1.2.3"\n'); self.stock.chmod(0o755)
        self.helper = self.base/'codex-code-mode-host'
        self.helper.write_text('#!/bin/sh\necho fixture-helper\n'); self.helper.chmod(0o755)
        self.m.config['stock'] = str(self.stock)
        self.modules = self.m.root/'modules'

    def test_discovery_does_not_execute_and_enable_new_is_opt_in(self):
        module(self.modules, runtime_env={'CODEX_UI_STATE_DIR': '~/fixture'})
        self.assertEqual(list(self.m.modules()), ['fixture'])
        self.assertEqual(self.m.selection('1.2.3'), [])
        self.m.config['enable_new'] = True
        self.assertEqual(len(self.m.selection('1.2.3')), 1)

    def test_native_plugin_updates_only_after_refresh_consent_and_keeps_owner(self):
        self.m.config['claude_install'] = {'ownership': 'native', 'plugin': 'info-pane@harness-ui-mods'}
        self.m.config['updates'] = 'ask'
        installed = {'status': 'unchanged', 'ownership': 'native', 'plugin_ref': 'info-pane@harness-ui-mods', 'enabled': False}
        with patch('codex_ui_mods.claude.install', return_value=installed) as inspect, \
             patch('codex_ui_mods.claude.update_native', return_value=dict(installed, status='updated')) as update, \
             patch.object(self.m, 'refresh') as refresh, patch('codex_ui_mods.core.sys_stdin_yes', return_value=False):
            self.assertEqual(self.m.update()['status'], 'pending')
            inspect.assert_not_called(); update.assert_not_called(); refresh.assert_not_called()
            self.m.update(yes=True, refresh=False)
            update.assert_not_called()
            self.m.update(yes=True, refresh=False, refresh_native=True)
            update.assert_called_once_with('claude', 'info-pane@harness-ui-mods')
            inspect.return_value = dict(installed, plugin_ref='info-pane@another-marketplace')
            with self.assertRaisesRegex(Error, 'owner changed'):
                self.m.sync_claude(refresh=True)
            self.assertEqual(update.call_count, 1)

    def test_codex_scoped_preparation_does_not_inspect_claude(self):
        self.m.config['claude_install'] = {'ownership': 'native'}
        with patch.object(self.m, 'sync_claude', side_effect=AssertionError('unrelated harness')):
            self.assertEqual(self.m.update(yes=True, refresh=False, include_claude=False)['status'], 'stock')

    def test_manifest_rejects_path_escape_and_injection_environment(self):
        outside = self.base/'outside.patch'; outside.write_text('x')
        p = module(self.modules, patches=['../../outside.patch'], tests=[{'package': 'x', 'filter': 'test'}])
        with self.assertRaises(Error): manifest(p/'module.json')
        atomic_json(p/'module.json', dict(json.loads((p/'module.json').read_text()), patches=[], runtime_env={'PYTHONPATH': '/bad'}))
        with self.assertRaises(Error): manifest(p/'module.json')

    def test_dependencies_compatibility_and_cycles(self):
        module(self.modules, dependencies=['second'])
        module(self.modules, 'second', order=100)
        self.assertEqual([m['id'] for m in select(self.m.modules(), ['fixture'], '1.2.3')], ['second', 'fixture'])
        with self.assertRaises(Error): select(self.m.modules(), ['fixture'], 'different')
        p = self.modules/'second/module.json'
        data = json.loads(p.read_text()); data['dependencies'] = ['fixture']; atomic_json(p, data)
        with self.assertRaisesRegex(Error, 'cycle'): select(self.m.modules(), ['fixture'], '1.2.3')

    def test_data_module_atomic_install_and_fallback_on_stock_change_or_tamper(self):
        module(self.modules, runtime_env={'CODEX_UI_STATE_DIR': '~/fixture'})
        self.m.config['enabled'] = ['fixture']
        result = self.m.update(refresh=False)
        self.assertEqual(result['status'], 'installed')
        binary, env = self.m.launch([])
        self.assertEqual(binary, str(self.stock.resolve()))
        self.assertTrue(env['CODEX_UI_STATE_DIR'].endswith('/fixture'))
        self.stock.write_text('#!/bin/sh\necho "codex-cli 1.2.4"\n')
        self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))

    def test_ask_unattended_does_not_fetch_or_build(self):
        self.m.config['updates'] = 'ask'
        with patch('codex_ui_mods.core.sys_stdin_yes', return_value=False), patch.object(self.m, 'refresh') as refresh:
            self.assertEqual(self.m.update()['status'], 'pending')
            refresh.assert_not_called()

    def test_lock_contention(self):
        with self.m.lock():
            with self.assertRaisesRegex(Error, 'another update'):
                with Manager(self.m.root).lock(): pass

    def git_fixture(self):
        repo = self.base/'remote'; repo.mkdir()
        run(['git', 'init', '-b', 'main', repo]); run(['git', '-C', repo, 'config', 'user.name', 'Fixture'])
        run(['git', '-C', repo, 'config', 'user.email', 'fixture@example.invalid'])
        (repo/'LICENSE').write_text('Apache License 2.0 fixture\n'); (repo/'NOTICE').write_text('Upstream fixture notice\n')
        (repo/'codex-rs').mkdir(); (repo/'codex-rs/fixture').write_text('before\n')
        run(['git', '-C', repo, 'add', '.']); run(['git', '-C', repo, 'commit', '-m', 'fixture'])
        run(['git', '-C', repo, 'tag', 'rust-v1.2.3'])
        self.m.config['upstream'] = str(repo)
        self.m.config['catalog_remote'] = str(repo)
        return repo

    def build_fixture(self, passed=2, smoke=None):
        self.git_fixture()
        p = module(self.modules, patches=['source.patch'], tests=[{'package': 'codex-tui', 'filter': 'fixture'}], smoke=smoke or [])
        (p/'source.patch').write_text('diff --git a/codex-rs/fixture b/codex-rs/fixture\n--- a/codex-rs/fixture\n+++ b/codex-rs/fixture\n@@ -1 +1 @@\n-before\n+after\n')
        self.m.config['enabled'] = ['fixture']
        tools = self.base/'tools'; tools.mkdir()
        cargo = tools/'cargo'
        cargo.write_text(f'#!{sys.executable}\nimport os,pathlib,sys\n'
                         f'if sys.argv[1]=="test": print("test result: ok. {passed} passed; 0 failed;")\n'
                         'else:\n p=pathlib.Path(os.environ["CARGO_TARGET_DIR"])/"release/codex";p.parent.mkdir(parents=True,exist_ok=True);p.write_text(\'#!/bin/sh\\necho "codex-cli 1.2.3"\\n\');p.chmod(0o755)\n')
        cargo.chmod(0o755)
        env = patch.dict(os.environ, {'PATH': str(tools)+os.pathsep+os.environ['PATH']}); env.start(); self.addCleanup(env.stop)

    def test_real_git_and_fake_cargo_install(self):
        self.build_fixture()
        result = self.m.update(refresh=False)
        self.assertEqual(result['status'], 'installed')
        record = self.m.current()
        self.assertEqual(record['profile'], 'release')
        self.assertEqual((Path(record['binary']).parent/'codex-code-mode-host').read_bytes(), self.helper.read_bytes())
        self.assertTrue((Path(record['binary']).parent/'upstream/NOTICE').is_file())
        source = Path(record['source'])
        self.assertEqual((source/'codex-rs/fixture').read_text(), 'after\n')
        (source/'unknown').write_text('keep me')
        self.m.build(self.m.stock(), self.m.selection('1.2.3'))
        self.assertEqual((source/'unknown').read_text(), 'keep me')
        self.assertEqual(len(list((self.m.root/'attempts').iterdir())), 2)

    def test_zero_tests_or_failed_smoke_never_activate(self):
        self.build_fixture(passed=0)
        with self.assertRaisesRegex(Error, 'no passing tests'): self.m.update(refresh=False)
        self.assertFalse((self.m.root/'current.json').exists())

    def test_no_build_refuses_missing_bundle_without_starting_compiler(self):
        self.build_fixture()
        with patch.object(self.m, 'build') as build:
            with self.assertRaisesRegex(Error, 'source build disabled'):
                self.m.update(refresh=False, no_build=True)
            build.assert_not_called()
        self.assertFalse((self.m.root/'current.json').exists())
        self.assertFalse((self.m.root/'attempts').exists())
        self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))

    def test_remote_availability_ignores_local_data_and_disabled_modules(self):
        from unittest.mock import MagicMock
        self.build_fixture()
        module(self.modules, 'local-data', codex=['*'])
        module(self.modules, 'disabled-view', codex=['*'])
        self.m.config['enabled'] += ['local-data', 'disabled-view']
        self.m.config['disabled'] = ['disabled-view']
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = 'https://example.invalid/catalog.json'
        response.read.return_value = json.dumps({'schema': 1, 'modules': [
            {'id': 'fixture', 'codex': ['1.2.4']}]}).encode()
        with patch('codex_ui_mods.core.urllib.request.urlopen', return_value=response), \
                patch.object(self.m, 'refresh') as refresh:
            result = self.m.availability(dict(self.m.stock(), version='1.2.4'))
        self.assertTrue(result['compatible_patch_ready'])
        self.assertTrue(result['metadata_only'])
        refresh.assert_not_called()

    def test_smoke_failure_never_activates(self):
        self.build_fixture(smoke=[[sys.executable, '-c', 'raise SystemExit(9)']])
        with self.assertRaises(Error): self.m.update(refresh=False)
        self.assertFalse((self.m.root/'current.json').exists())

    def test_refresh_fast_forward_preserves_dirty_and_diverged_checkout(self):
        remote = self.git_fixture()
        self.m.refresh()
        (self.m.root/'catalog/unknown').write_text('keep')
        with self.assertRaisesRegex(Error, 'local edits'): self.m.refresh()
        self.assertEqual((self.m.root/'catalog/unknown').read_text(), 'keep')
        (self.m.root/'catalog/unknown').unlink()
        (remote/'next').write_text('next'); run(['git', '-C', remote, 'add', '.']); run(['git', '-C', remote, 'commit', '-m', 'next'])
        self.assertEqual(self.m.refresh(), run(['git', '-C', remote, 'rev-parse', 'HEAD']).strip())
        self.m.config['catalog_remote'] = 'https://example.invalid/changed'
        with self.assertRaisesRegex(Error, 'remote differs'): self.m.refresh()

    def test_refresh_never_executes_a_synthetic_unapproved_controller(self):
        target = self.m.root/'catalog/src/codex_ui_mods/cli.py'
        target.parent.mkdir(parents=True); target.write_text('# synthetic')
        with patch.object(self.m, 'refresh') as refresh, patch('codex_ui_mods.cli.os.execve') as execute:
            cli.refreshed_manager(self.m)
        refresh.assert_called_once_with(); execute.assert_not_called()

    def test_artifact_hash_match_install_and_mismatch_refusal(self):
        import hashlib
        import io
        import platform
        import tarfile
        from unittest.mock import MagicMock
        plugin = module(self.modules, patches=['source.patch'], tests=[{'package':'codex-tui', 'filter':'fixture'}])
        (plugin/'source.patch').write_text('fixture patch')
        self.m.config['enabled'] = ['fixture']
        stock, modules = self.m.stock(), self.m.selection('1.2.3')
        binary = b'#!/bin/sh\necho "codex-cli 1.2.3"\n'
        wanted = {m['id']: m['patch_hash'] for m in modules if m['patches']}
        metadata = {'schema': 1, 'codex': '1.2.3', 'modules': wanted,
                    'upstream_commit': 'a'*40, 'binary_sha256': hashlib.sha256(binary).hexdigest()}
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for name, data in {'codex': binary, 'upstream/LICENSE': b'Apache License fixture',
                               'upstream/NOTICE': b'Upstream notice', 'source.json': json.dumps(metadata).encode()}.items():
                info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        data = output.getvalue()
        bundle = {'codex': '1.2.3', 'platform': platform.system().lower()+'-'+platform.machine().lower(),
                  'modules': wanted, 'url': 'https://example.invalid/codex.tar.gz', 'sha256': hashlib.sha256(data).hexdigest()}
        atomic_json(self.m.root/'catalog/catalog.json', {'bundles': [bundle]})
        def response(*args, **kwargs):
            r = MagicMock(); r.__enter__.return_value = r
            r.read.side_effect = io.BytesIO(data).read; r.geturl.return_value = bundle['url']
            return r
        with patch('codex_ui_mods.core.urllib.request.urlopen', response):
            record = self.m.artifact(stock, modules)
        self.assertEqual(record['profile'], 'artifact')
        self.assertEqual((Path(record['binary']).parent/'codex-code-mode-host').read_bytes(), self.helper.read_bytes())
        self.assertIn('Modified by Harness UI Mods', (Path(record['binary']).parent/'upstream/NOTICE').read_text())
        self.assertEqual(record['archive_sha256'], bundle['sha256'])
        bundle['sha256'] = '0'*64
        atomic_json(self.m.root/'catalog/catalog.json', {'bundles': [bundle]})
        with patch('codex_ui_mods.core.urllib.request.urlopen', response):
            with self.assertRaisesRegex(Error, 'SHA256 mismatch'): self.m.artifact(stock, modules)
        self.assertEqual(self.m.current()['sha256'], record['sha256'])

    def test_stock_change_during_build_does_not_activate(self):
        self.build_fixture()
        original = self.m.stock()
        with patch.object(self.m, 'stock', return_value=dict(original, version='1.2.4')):
            with self.assertRaisesRegex(Error, 'stock changed'):
                self.m.build(original, self.m.selection('1.2.3'))
        self.assertFalse((self.m.root/'current.json').exists())

    def test_cli_ask_no_consent_does_not_refresh_executable_code(self):
        self.m.config['updates'] = 'ask'; self.m.save()
        with patch('codex_ui_mods.core.sys_stdin_yes', return_value=False), \
             patch.object(Manager, 'refresh') as refresh, patch.object(Manager, 'availability', return_value={'metadata_only': True}), \
             patch('builtins.print'):
            self.assertEqual(cli.main(['--home', str(self.m.root), 'update']), 2)
        refresh.assert_not_called()

    def test_inherited_builtin_flags_are_disabled_without_module(self):
        with patch.dict(os.environ, {'CODEX_UI_INFO_PANE': '1', 'CODEX_UI_CONTEXT_DRAWER': '1'}):
            binary, env = self.m.launch([])
        self.assertEqual(env['CODEX_UI_INFO_PANE'], '0')
        self.assertEqual(env['CODEX_UI_CONTEXT_DRAWER'], '0')

    def test_corrupt_current_metadata_falls_back(self):
        self.m.root.mkdir(parents=True)
        (self.m.root/'current.json').write_text('not JSON')
        self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))

    def test_bad_manifest_and_io_error_degrade_launch_to_stock(self):
        p = module(self.modules)
        m = json.loads((p/'module.json').read_text()); m['id'] = 7
        atomic_json(p/'module.json', m)
        self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))
        with patch.object(self.m, 'modules', side_effect=OSError('denied')):
            self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))

    def test_data_only_module_must_pass_smoke(self):
        module(self.modules, smoke=[[sys.executable, '-c', 'raise SystemExit(8)']])
        self.m.config['enabled'] = ['fixture']
        with self.assertRaises(Error): self.m.update(refresh=False)
        self.assertFalse((self.m.root/'current.json').exists())

    def test_explicit_disable_wins_over_enable_new(self):
        module(self.modules)
        self.m.config.update(enable_new=True, disabled=['fixture'])
        self.assertEqual(self.m.selection('1.2.3'), [])
        self.m.save()
        with patch('builtins.print'):
            self.assertEqual(cli.main(['--home', str(self.m.root), 'modules', 'enable', 'fixture']), 0)
        self.assertEqual(len(Manager(self.m.root).selection('1.2.3')), 1)
        with patch('builtins.print'):
            cli.main(['--home', str(self.m.root), 'modules', 'disable', 'fixture'])
        self.assertEqual(Manager(self.m.root).selection('1.2.3'), [])

    def test_helper_assets_are_part_of_content_hash(self):
        p = module(self.modules)
        (p/'helper.py').write_text('first')
        first = manifest(p/'module.json')['content_hash']
        (p/'helper.py').write_text('changed')
        self.assertNotEqual(first, manifest(p/'module.json')['content_hash'])
        first = manifest(p/'module.json')['content_hash']
        cache = p/'__pycache__'; cache.mkdir(); (cache/'ignored.pyc').write_bytes(b'cache')
        self.assertEqual(first, manifest(p/'module.json')['content_hash'])

    def test_bundle_rejects_traversal_links_duplicates_missing_notices_and_raw_binary(self):
        import io
        import tarfile
        from codex_ui_mods.core import extract_bundle
        for names, link in [(['../escape'], False), (['/absolute'], False), (['codex'], True),
                            (['codex', 'codex'], False), (['codex'], False)]:
            with self.subTest(names=names, link=link):
                archive = self.base/'test.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    for name in names:
                        info = tarfile.TarInfo(name)
                        if link:
                            info.type = tarfile.SYMTYPE; info.linkname = '/outside'
                        else:
                            info.size = 1
                        tar.addfile(info, None if link else io.BytesIO(b'x'))
                with tempfile.TemporaryDirectory() as destination:
                    with self.assertRaises(Error): extract_bundle(archive, Path(destination))
        archive.write_bytes(b'#!/bin/sh\necho raw')
        with tempfile.TemporaryDirectory() as destination:
            with self.assertRaises(Error): extract_bundle(archive, Path(destination))

    def test_matching_helper_preserved_and_tamper_falls_back(self):
        self.build_fixture()
        record = self.m.update(refresh=False)['installation']
        adjacent = Path(record['binary']).parent/'codex-code-mode-host'
        self.assertEqual(adjacent.read_bytes(), self.helper.read_bytes())
        self.assertTrue(record['helper_sha256'])
        adjacent.write_text('tampered')
        self.assertEqual(self.m.launch([])[0], str(self.stock.resolve()))

    def test_missing_modern_stock_helper_refuses_activation(self):
        self.build_fixture()
        self.helper.unlink()
        with self.assertRaisesRegex(Error, 'CodeMode|code-mode-host'):
            self.m.update(refresh=False)
        self.assertFalse((self.m.root/'current.json').exists())

    def test_data_only_relative_wrapper_uses_original_layout_and_runtime_env(self):
        (self.base/'version').write_text('codex-cli 1.2.3\n')
        self.stock.write_text('#!/bin/sh\ncat "$(dirname "$0")/version"\n')
        self.helper.unlink()
        module(self.modules, runtime_env={'CODEX_UI_FIXTURE': 'working'}, smoke=[
            [sys.executable, '-c', 'import os,subprocess,sys; assert os.environ["CODEX_UI_FIXTURE"]=="working"; assert b"1.2.3" in subprocess.check_output([sys.argv[1],"--version"])', '{binary}']])
        self.m.config['enabled'] = ['fixture']
        record = self.m.update(refresh=False)['installation']
        self.assertEqual(record['binary'], str(self.stock.resolve()))
        binary, env = self.m.launch([])
        self.assertIn('1.2.3', run([binary, '--version'], env=env))
        self.assertEqual(env['CODEX_UI_FIXTURE'], 'working')
        self.assertTrue(self.m.current())

    def test_malformed_remote_metadata_is_advisory_error(self):
        import io
        from unittest.mock import MagicMock
        for metadata in ([], {'schema': 1, 'modules': None}, {'schema': 1, 'modules': [{'id': 'fixture', 'codex': None}]},
                         {'schema': 1, 'modules': [{'id': 7, 'codex': ['1.2.3']}]}, {'schema': 1, 'modules': [None]}):
            response = MagicMock(); response.__enter__.return_value = response
            response.read.return_value = json.dumps(metadata).encode()
            response.geturl.return_value = 'https://example.invalid/catalog.json'
            with self.subTest(metadata=metadata), patch('codex_ui_mods.core.urllib.request.urlopen', return_value=response):
                with self.assertRaises(Error): self.m.availability(self.m.stock())
        self.m.config['updates'] = 'ask'; self.m.save()
        with patch.object(Manager, 'availability', side_effect=Error('invalid metadata')), \
             patch('codex_ui_mods.cli.os.execve', side_effect=RuntimeError('launched stock')) as execute, patch('builtins.print'):
            with self.assertRaisesRegex(RuntimeError, 'launched stock'):
                cli.main(['--home', str(self.m.root), 'launch'])
            self.assertEqual(execute.call_args.args[0], str(self.stock.resolve()))

    def test_claude_modules_never_enter_codex_selection(self):
        module(self.modules, harness='claude', runtime_env={'HARNESS_UI_STATE_DIR': '/fixture'})
        self.m.config['enabled'] = ['fixture']
        self.assertEqual(self.m.selection('1.2.3'), [])
        self.m.config['enable_new'] = True
        self.assertEqual(self.m.selection('1.2.3'), [])

    def test_neutral_home_default_and_explicit_legacy_alias(self):
        with patch.dict(os.environ, {'HARNESS_UI_HOME': str(self.base/'neutral'), 'CODEX_UI_HOME': str(self.base/'legacy')}):
            self.assertEqual(Manager().root, self.base/'neutral')
        with patch.dict(os.environ, {'CODEX_UI_HOME': str(self.base/'legacy')}):
            self.assertEqual(Manager().root, self.base/'legacy')

    def test_runtime_only_module_changes_reuse_patch_binary_without_cargo(self):
        self.build_fixture()
        first = self.m.update(refresh=False)['installation']
        module(self.modules, 'private-data', codex=['*'], runtime_env={'HARNESS_UI_STATE_DIR': '/fixture/new-state'})
        self.m.config['enabled'].append('private-data')
        with patch.object(self.m, 'build', side_effect=AssertionError('unnecessary rebuild')), patch.object(self.m, 'artifact', side_effect=AssertionError('unnecessary download')):
            refreshed = self.m.update(refresh=False)
        self.assertEqual(refreshed['status'], 'runtime-refreshed')
        self.assertEqual(first['patch_fingerprint'], refreshed['installation']['patch_fingerprint'])
        self.assertNotEqual(first['fingerprint'], refreshed['installation']['fingerprint'])
        self.assertEqual(self.m.launch([])[1]['HARNESS_UI_STATE_DIR'], '/fixture/new-state')
        source = json.loads((Path(refreshed['installation']['binary']).parent/'source.json').read_text())
        self.assertEqual(set(source['modules']), {'fixture'})

    def test_runtime_environment_does_not_change_patch_identity(self):
        self.build_fixture()
        path = self.modules/'fixture/module.json'
        first = manifest(path)
        value = json.loads(path.read_text()); value['runtime_env'] = {'HARNESS_UI_STATE_DIR': '/different/runtime'}
        atomic_json(path, value)
        second = manifest(path)
        self.assertEqual(first['patch_hash'], second['patch_hash'])
        self.assertNotEqual(first['content_hash'], second['content_hash'])

    def test_stock_fingerprint_change_bypasses_six_hour_auto_throttle(self):
        import time
        stock = self.m.stock()
        self.m.config['updates'] = 'auto'; self.m.save()
        atomic_json(self.m.root/'last-auto-check.json', {'stock': stock, 'checked': time.time()})
        with patch('codex_ui_mods.cli.subprocess.Popen') as spawn, \
             patch.object(Manager, 'stock', return_value=stock) as installed, \
             patch('codex_ui_mods.cli.os.execve', side_effect=RuntimeError('launched')), patch('builtins.print'):
            with self.assertRaises(RuntimeError): cli.main(['--home', str(self.m.root), 'launch'])
            spawn.assert_not_called()
            installed.return_value = dict(stock, version='1.2.4', sha256='changed')
            with self.assertRaises(RuntimeError): cli.main(['--home', str(self.m.root), 'launch'])
            spawn.assert_called_once()

    def test_explicit_state_environment_matches_launch_and_controller(self):
        from codex_ui_mods.surface import resolve_state
        module(self.modules, runtime_env={'HARNESS_UI_STATE_DIR':str(self.base/'configured')})
        self.m.config['enabled'] = ['fixture']; self.m.update(refresh=False)
        with patch.dict(os.environ, {'HARNESS_UI_STATE_DIR':str(self.base/'explicit')}):
            _, environment = self.m.launch([])
            resolved = resolve_state('codex', self.m)
        self.assertEqual(environment['HARNESS_UI_STATE_DIR'], resolved['state_root'])
        self.assertEqual(environment['HARNESS_UI_STATE_DIR'], str(self.base/'explicit'))
        self.assertEqual(environment['CODEX_UI_INFO_PANE'], '0')


if __name__ == '__main__': unittest.main()
