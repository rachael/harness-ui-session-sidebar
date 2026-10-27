import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('export_repos', Path(__file__).resolve().parents[1]/'tools/export_repos.py')
e = importlib.util.module_from_spec(spec); spec.loader.exec_module(e)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'source'; self.root.mkdir()
        self.git('init', '-b', 'main'); self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        files = {'.claude-plugin/marketplace.json':'{"name":"harness-ui-mods","plugins":[]}', '.gitignore':'__pycache__/\n*.pyc\n', 'README.md': 'Shared source', 'LICENSE': 'MIT fixture', 'NOTICE': 'Upstream notice fixture',
                 'LICENSES/Apache-2.0.txt': 'Apache fixture', 'assets/sprites.json': '{}', 'contracts/overview.md': 'Shared contract', 'pyproject.toml': 'fixture', 'src/codex_ui_mods/cli.py': 'print("fixture CLI")\n',
                 'modules/session-sidebar/module.json': '{}', 'modules/info-pane/module.json': '{}', 'modules/context-drawer/source.patch': 'fixture',
                 'adapters/claude-code/.claude-plugin/plugin.json': '{}', 'private.txt': 'must stay private', 'site/template.html': '<html><head><title>{{TITLE}}</title></head><body>{{REPOSITORY}}</body></html>',
                 'site/demo.js': 'fixture', 'site/style.css': 'fixture', 'docs/guide.md': 'Fixture guide',
                 'site/native/pinned-line.png': 'fixture', 'site/native/hover-drawer.png': 'fixture'}
        for name, content in files.items():
            path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        self.git('add', '.'); self.git('commit', '-m', 'synthetic fixture')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True).stdout

    def test_clean_exports_have_crosslinks_hashes_no_history_or_untracked_files(self):
        (self.root/'modules/info-pane/untracked-secret').write_text('never export')
        (self.root/'src/codex_ui_mods/cli.py').write_text('unreviewed change')
        out = self.root.parent/'exports'; reports = e.export(self.root, out)
        self.assertEqual(len(reports), 7)
        for repository,module in (('harness-ui-context-drawer','context-drawer'),('harness-ui-session-sidebar','session-sidebar')):
            bootstrap=(out/repository/'install.py').read_text()
            self.assertIn(repr(module),bootstrap)
            self.assertIn('preserved',bootstrap)
        with __import__('zipfile').ZipFile(out/'harness-ui-mods/docs/downloads/harness-ui-full-source.zip') as full:
            self.assertNotIn(b'--all-modules',full.read('install.py'))
        self.assertEqual((out/'harness-ui-mods/src/codex_ui_mods/cli.py').read_text(), 'print("fixture CLI")\n')
        self.assertFalse(list(out.rglob('.git')))
        self.assertFalse(list(out.rglob('private.txt')))
        self.assertFalse(list(out.rglob('untracked-secret')))
        self.assertTrue((out/'claude-code-info-pane/.claude-plugin/marketplace.json').is_file())
        self.assertTrue((out/'harness-ui-mods/assets/sprites.json').is_file())
        self.assertTrue((out/'claude-code-info-pane/contracts/overview.md').is_file())
        self.assertTrue((out/'codex-info-pane/assets/sprites.json').is_file())
        self.assertIn(e.AUTHORITY, (out/'codex-info-pane/README.md').read_text())
        metadata = json.loads((out/'codex-info-pane/SOURCE.json').read_text())
        self.assertEqual(len(metadata['source_commit']), 40)
        self.assertIn('install.py', metadata['files'])
        self.assertTrue((out/'codex-tui-hud-extender-statusline/src/codex_ui_mods/cli.py').is_file())
        self.assertTrue((out/'claude-code-info-pane/modules/info-pane/module.json').is_file())
        self.assertTrue((out/'codex-info-pane/adapters/claude-code/.claude-plugin/plugin.json').is_file())
        self.assertTrue((out/'codex-info-pane/docs/index.html').is_file())
        import zipfile,hashlib
        archive = out/'codex-info-pane/docs/downloads/harness-ui-info-pane-source.zip'
        with zipfile.ZipFile(archive) as zipped:
            preset = json.loads(zipped.read('SOURCE.json'))
            self.assertEqual(preset['files']['install.py'], hashlib.sha256(zipped.read('install.py')).hexdigest())
            self.assertIn(b'info-pane', zipped.read('install.py'))
            self.assertIn('src/codex_ui_mods/cli.py', zipped.namelist())
        with zipfile.ZipFile(out/'codex-info-pane/docs/downloads/harness-ui-session-sidebar-source.zip') as zipped:
            self.assertIn(b'session-sidebar', zipped.read('install.py'))
            self.assertIn('modules/session-sidebar/module.json', zipped.namelist())
            self.assertIn('adapters/claude-code/.claude-plugin/plugin.json', zipped.namelist())
        compile((out/'claude-code-info-pane/install.py').read_text(), 'install.py', 'exec')
        with self.assertRaises(FileExistsError): e.export(self.root, out)

    def test_every_landing_page_explains_the_optional_sidebar_demo_before_controls(self):
        template=(Path(__file__).resolve().parents[1]/'site/template.html').read_text()
        (self.root/'site/template.html').write_text(template)
        self.git('add','.');self.git('commit','-m','shared landing fixture')
        out=self.root.parent/'landing-exports';e.export(self.root,out)
        for repository in e.TITLES:
            html=(out/repository/'docs/index.html').read_text()
            self.assertIn('optional shared session sidebar and its associated machine-local sharing are off by default',html)
            self.assertIn('only for this synthetic demo',html)
            self.assertIn('id="sidebar-enabled" aria-describedby="sharing-description"',html)
            self.assertLess(html.index('id="sharing-description"'),html.index('id="sidebar-enabled"'))

    def test_export_refuses_selected_symlink_and_missing_license(self):
        (self.root/'modules/info-pane/link').symlink_to('/outside')
        self.git('add', '.'); self.git('commit', '-m', 'symlink fixture')
        with self.assertRaisesRegex(ValueError, 'symlinks'): e.export(self.root, self.root.parent/'bad')
        self.git('rm', 'modules/info-pane/link', 'LICENSES/Apache-2.0.txt'); self.git('commit', '-m', 'missing license fixture')
        with self.assertRaisesRegex(ValueError, 'license files missing'): e.export(self.root, self.root.parent/'missing')

    def test_executed_exported_catalog_cache_is_ignored_but_real_edits_refused(self):
        import os,sys
        from codex_ui_mods.core import Manager, Error
        out = self.root.parent/'executed-export'
        e.export(self.root, out, variants=['harness-ui-mods'])
        source = out/'harness-ui-mods'
        def git(*args):
            return subprocess.run(['git','-C',str(source),*args],check=True,capture_output=True)
        git('init','-b','main'); git('config','user.name','Fixture'); git('config','user.email','fixture@example.invalid')
        git('add','.'); git('commit','-m','reviewed export fixture')
        manager = Manager(self.root.parent/'managed')
        manager.config['catalog_remote'] = str(source)
        manager.refresh()
        catalog = manager.root/'catalog'
        subprocess.run([sys.executable,'-m','codex_ui_mods.cli'],env=dict(os.environ,PYTHONPATH=str(catalog/'src')),check=True,capture_output=True)
        self.assertTrue(list((catalog/'src').rglob('__pycache__')))
        manager.refresh()
        (catalog/'untracked-local.txt').write_text('preserve')
        with self.assertRaisesRegex(Error,'local edits'): manager.refresh()
        self.assertEqual((catalog/'untracked-local.txt').read_text(),'preserve')
