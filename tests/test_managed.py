import json
from pathlib import Path
import tempfile
import unittest

from codex_ui_mods.core import Error, atomic_json
from codex_ui_mods.managed import install_tree


class ManagedPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.destination = self.root/'source', self.root/'installed'
        atomic_json(self.source/'.claude-plugin/plugin.json', {'name': 'info-pane', 'version': '1.0'})
        (self.source/'entry.py').write_text('fixture plugin\n')

    def test_install_idempotent_then_backup_on_clean_upgrade(self):
        self.assertEqual(install_tree(self.source, self.destination)['status'], 'installed')
        self.assertEqual(install_tree(self.source, self.destination)['status'], 'unchanged')
        (self.source/'entry.py').write_text('updated plugin\n')
        result = install_tree(self.source, self.destination)
        self.assertEqual((Path(result['backup'])/'entry.py').read_text(), 'fixture plugin\n')
        self.assertEqual((self.destination/'entry.py').read_text(), 'updated plugin\n')

    def test_unknown_files_and_unmanaged_destinations_are_preserved(self):
        install_tree(self.source, self.destination)
        (self.destination/'local.txt').write_text('preserve me')
        with self.assertRaisesRegex(Error, 'unknown edits'): install_tree(self.source, self.destination)
        self.assertEqual((self.destination/'local.txt').read_text(), 'preserve me')
        unmanaged = self.root/'unmanaged'; unmanaged.mkdir()
        with self.assertRaisesRegex(Error, 'not managed'): install_tree(self.source, unmanaged)

    def test_changed_managed_file_preserved(self):
        install_tree(self.source, self.destination)
        (self.destination/'entry.py').write_text('local edit')
        with self.assertRaisesRegex(Error, 'unknown edits'): install_tree(self.source, self.destination)
        self.assertEqual((self.destination/'entry.py').read_text(), 'local edit')

    def test_symlink_and_wrong_plugin_refused(self):
        (self.source/'link').symlink_to('/outside')
        with self.assertRaisesRegex(Error, 'symlinks'): install_tree(self.source, self.destination)
        (self.source/'link').unlink()
        atomic_json(self.source/'.claude-plugin/plugin.json', {'name': 'unrelated', 'version': '1.0'})
        with self.assertRaisesRegex(Error, 'info-pane'): install_tree(self.source, self.destination)

    def test_explicit_matching_baseline_adoption_backs_up_and_mismatch_refuses(self):
        import shutil
        shutil.copytree(self.source, self.destination)
        result = install_tree(self.source, self.destination, adopt_matching=True)
        self.assertTrue(Path(result['backup']).is_dir())
        other = self.root/'other'; shutil.copytree(self.source, other)
        (other/'entry.py').write_text('unknown edit')
        with self.assertRaisesRegex(Error, 'matching baseline'):
            install_tree(self.source, other, adopt_matching=True)
