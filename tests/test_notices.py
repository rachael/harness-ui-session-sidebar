from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Manager
from codex_ui_mods.notices import baseline, notice


class NoticeTests(unittest.TestCase):
    def test_once_reminder_dismiss_and_suppress_without_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(Path(directory)/'home')
            modules = {'first': {'patches':['fixture']}}
            with patch.object(manager, 'modules', side_effect=lambda: dict(modules)), patch('codex_ui_mods.notices.shutil.which', return_value=None):
                baseline(manager)
                modules['second'] = {'patches':['fixture']}
                self.assertEqual(notice(manager, now=10)['features'], ['codex:second'])
                self.assertEqual(notice(manager, now=11)['features'], [])
                notice(manager, 'remind-later', hours=1, now=20)
                self.assertEqual(notice(manager, now=30)['features'], [])
                self.assertEqual(notice(manager, now=3621)['features'], ['codex:second'])
                self.assertEqual(notice(manager, now=3622)['features'], [])
                notice(manager, 'suppress')
                modules['third'] = {'patches':['fixture']}
                self.assertEqual(notice(manager)['features'], [])
                notice(manager, 'resume')
                self.assertIn('codex:third', notice(manager)['features'])
                notice(manager, 'dismiss')
                self.assertEqual(notice(manager)['features'], [])
            self.assertEqual(manager.config['enabled'], [])
            self.assertFalse(manager.config['enable_new'])
