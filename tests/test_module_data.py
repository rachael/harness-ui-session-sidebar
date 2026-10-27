"""Shared data embedded in upstream patches must match its editable source."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SharedModuleDataTests(unittest.TestCase):
    def test_codex_sprite_catalog_matches_shared_source(self):
        target = 'codex-rs/tui/src/info_pane/assets/sprites.json'
        lines = (ROOT/'modules/info-pane/source.patch').read_text().splitlines()
        collecting = False
        content = []
        for line in lines:
            if line.startswith('diff --git '):
                collecting = False
            elif line == '+++ b/' + target:
                collecting = True
            elif collecting and line.startswith('+'):
                content.append(line[1:])
        self.assertTrue(content, 'sprite asset missing from native module')
        self.assertEqual(json.loads('\n'.join(content)),
                         json.loads((ROOT/'assets/sprites.json').read_text()))


if __name__ == '__main__':
    unittest.main()
