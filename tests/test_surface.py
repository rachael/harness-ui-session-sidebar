import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_ui_mods.core import Error, atomic_json
from codex_ui_mods.surface import surface


class SurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base/'state'
        self.path = self.root/'sessions/codex/session.json'
        env = patch.dict(os.environ, {'HARNESS_UI_HOME': str(self.base/'harness-home'), 'CODEX_UI_STATE_DIR': str(self.root), 'PATH': os.environ.get('PATH', '/usr/bin:/bin')}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.previous = os.environ.pop('CODEX_UI_PUBLISH_COMMAND', None)
        self.addCleanup(self.restore_publisher)

    def restore_publisher(self):
        os.environ.pop('CODEX_UI_PUBLISH_COMMAND', None)
        if self.previous is not None: os.environ['CODEX_UI_PUBLISH_COMMAND'] = self.previous

    def call(self, **kwargs):
        return surface('ask', 'session', 'Choose a color?', 'message:fixture-7', **kwargs)

    def test_raw_publish_readback_dedup_and_preserve_other_content(self):
        atomic_json(self.path, {'title': 'Keep', 'views': {'main': {'tasks': [{'subject': 'Keep task'}]}}})
        published = self.call()
        self.assertEqual(published['result'], 'published')
        again = self.call()
        self.assertEqual(again, dict(published, result='already_present'))
        record = json.loads(self.path.read_text())
        self.assertEqual(record['title'], 'Keep')
        self.assertEqual(record['views']['main']['tasks'][0]['subject'], 'Keep task')
        self.assertEqual(len(record['surfaced']), 1)
        self.assertEqual(record['surfaced'][0]['surfaced_by'], 'codex')
        self.assertTrue(record['surfaced'][0]['verified'])

    def test_same_id_different_content_refused(self):
        self.call(identity='fixture')
        with self.assertRaisesRegex(Error, 'different content'):
            surface('note', 'session', 'Other content', 'message:other', 'fixture')

    def test_resolve_and_dedup_does_not_reopen(self):
        item = self.call()
        result = surface('resolve', 'session', identity=item['id'])
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(surface('resolve', 'session', identity=item['id'])['result'], 'already_present')
        with self.assertRaisesRegex(Error, 'resolved'): self.call()

    def test_envelope_without_publisher_never_rewritten(self):
        atomic_json(self.path, {'schema_version': 1, 'revision': 'one', 'data': {'title': 'Keep'}, 'provenance': ['keep']})
        original = self.path.read_bytes()
        with self.assertRaisesRegex(Error, 'requires HARNESS_UI_PUBLISH_COMMAND'): self.call()
        self.assertEqual(self.path.read_bytes(), original)

    def test_publisher_cas_readback_each_stage_and_envelope_preservation(self):
        atomic_json(self.path, {'schema_version': 1, 'revision': '0', 'data': {'title': 'Keep'}, 'provenance': ['keep']})
        publisher = self.base/'publisher.py'
        log = self.base/'published.jsonl'
        publisher.write_text(
            'import json,os,pathlib,sys\n'
            f'path=pathlib.Path({str(self.path)!r});log=pathlib.Path({str(log)!r})\n'
            'old=json.loads(path.read_text());record=json.load(sys.stdin)\n'
            'assert os.environ["CODEX_UI_EXPECT_REVISION"]==old["revision"]\n'
            'assert sys.argv[1:]==["codex","session"]\n'
            'with log.open("a") as f:f.write(json.dumps(record)+"\\n")\n'
            'old["data"]=record;old["revision"]=str(int(old["revision"])+1);path.write_text(json.dumps(old))\n')
        os.environ['CODEX_UI_PUBLISH_COMMAND'] = json.dumps([sys.executable, str(publisher), '{provider}', '{session}'])
        self.assertEqual(self.call()['result'], 'published')
        writes = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([entry['surfaced'][0]['verified'] for entry in writes], [False, True])
        envelope = json.loads(self.path.read_text())
        self.assertEqual(envelope['revision'], '2')
        self.assertEqual(envelope['provenance'], ['keep'])
        self.assertEqual(envelope['data']['title'], 'Keep')
        self.assertEqual(self.call()['result'], 'already_present')
        self.assertEqual(len(log.read_text().splitlines()), 2)

    def test_success_exit_without_persistence_not_verified(self):
        os.environ['CODEX_UI_PUBLISH_COMMAND'] = json.dumps([sys.executable, '-c', 'pass'])
        with self.assertRaisesRegex(Error, 'did not persist'): self.call()
        self.assertFalse(self.path.exists())

    def test_cas_conflict_returns_retryable_error(self):
        os.environ['CODEX_UI_PUBLISH_COMMAND'] = json.dumps([sys.executable, '-c', 'raise SystemExit(3)'])
        with self.assertRaisesRegex(Error, 'reread/retry'): self.call()

    def test_invalid_state_and_identifiers_preserved(self):
        for state in ([], {'surfaced': None}, {'schema_version': 1, 'revision': 'one', 'data': []}):
            atomic_json(self.path, state)
            original = self.path.read_bytes()
            with self.assertRaises(Error): self.call()
            self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(Error): surface('note', '../escape', 'text', 'message:1')

    def test_existing_unverified_content_is_confirmed(self):
        item = self.call()
        value = json.loads(self.path.read_text()); value['surfaced'][0]['verified'] = False
        atomic_json(self.path, value)
        self.assertTrue(self.call()['verified'])

    def test_concurrent_surface_lock_returns_retry(self):
        import fcntl
        self.path.parent.mkdir(parents=True)
        with self.path.with_suffix('.surface.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Error, 'retry'): self.call()

    def test_neutral_aliases_provider_badge_and_shared_session_publisher(self):
        from codex_ui_mods.surface import publish_session
        neutral = self.base/'neutral'
        with patch.dict(os.environ, {'HARNESS_UI_STATE_DIR': str(neutral), 'HARNESS_UI_BADGES': '{"claude":"star"}'}):
            item = surface('note', 'session', 'A shared note', 'message:99', provider='claude')
            path = neutral/'sessions/claude/session.json'
            record = json.loads(path.read_text())
            self.assertEqual(record['surfaced'][0]['surfaced_by'], 'claude')
            self.assertEqual(record['surfaced'][0]['badge'], 'star')
            incoming = self.base/'incoming.json'; atomic_json(incoming, {'session': 'session', 'title': 'Authoritative title'})
            result = publish_session('claude', 'session', incoming, ['surfaced'], consume=True)
            self.assertTrue(result['verified']); self.assertFalse(incoming.exists())
            saved = json.loads(path.read_text())
            self.assertEqual(saved['title'], 'Authoritative title')
            self.assertEqual(saved['surfaced'][0]['id'], item['id'])

    def test_session_publisher_rejects_nonobject_and_revision_mismatch(self):
        from codex_ui_mods.surface import publish_session
        incoming = self.base/'incoming.json'; atomic_json(incoming, [])
        with self.assertRaisesRegex(Error, 'object'): publish_session('codex', 'session', incoming)
        atomic_json(incoming, {'session': 'session'})
        with patch.dict(os.environ, {'HARNESS_UI_EXPECT_REVISION': 'wrong'}):
            with self.assertRaisesRegex(Error, 'revision changed'): publish_session('codex', 'session', incoming)
        self.assertTrue(incoming.exists())

    def test_merge_keeps_other_writers_fields_and_default_still_replaces(self):
        from codex_ui_mods.surface import publish_session
        path = self.root/'sessions/claude/session.json'
        atomic_json(path, {'title': 'Pane', 'terminal': {'tty': '/dev/ttys001'}, 'surfaced': [{'id': 'a'}], 'usage': {'input': 1}})
        incoming = self.base/'usage.json'
        atomic_json(incoming, {'session': 'session', 'usage': {'input': 5, 'output': 2}})
        self.assertTrue(publish_session('claude', 'session', incoming, merge=True)['verified'])
        self.assertEqual(json.loads(path.read_text()), {'title': 'Pane', 'terminal': {'tty': '/dev/ttys001'}, 'surfaced': [{'id': 'a'}],
                                                        'usage': {'input': 5, 'output': 2}, 'session': 'session'})
        # without merge the input replaces the record, keeping only retained fields
        atomic_json(incoming, {'session': 'session', 'title': 'Pane 2'})
        publish_session('claude', 'session', incoming, ['surfaced', 'usage', 'terminal', 'presence'])
        self.assertEqual(json.loads(path.read_text()), {'session': 'session', 'title': 'Pane 2', 'surfaced': [{'id': 'a'}],
                                                        'usage': {'input': 5, 'output': 2}, 'terminal': {'tty': '/dev/ttys001'}})

    def test_merge_goes_through_the_configured_publisher_with_cas(self):
        from codex_ui_mods.surface import publish_session
        path = self.root/'sessions/codex/session.json'
        atomic_json(path, {'schema_version': 1, 'revision': '4', 'data': {'title': 'Keep', 'views': {'main': {}}}})
        publisher = self.base/'publisher.py'
        publisher.write_text(
            'import json,os,pathlib,sys\n'
            f'path=pathlib.Path({str(path)!r})\n'
            'old=json.loads(path.read_text());record=json.load(sys.stdin)\n'
            'assert os.environ["HARNESS_UI_EXPECT_REVISION"]==old["revision"]\n'
            'old["data"]=record;old["revision"]=str(int(old["revision"])+1);path.write_text(json.dumps(old))\n')
        os.environ['CODEX_UI_PUBLISH_COMMAND'] = json.dumps([sys.executable, str(publisher), '{provider}', '{session}'])
        incoming = self.base/'presence.json'
        atomic_json(incoming, {'presence': {'state': 'working'}})
        publish_session('codex', 'session', incoming, merge=True)
        envelope = json.loads(path.read_text())
        self.assertEqual(envelope['revision'], '5')
        self.assertEqual(envelope['data'], {'title': 'Keep', 'views': {'main': {}}, 'presence': {'state': 'working'}})
        # a stale expected revision is refused, nothing written
        with patch.dict(os.environ, {'HARNESS_UI_EXPECT_REVISION': '4'}):
            with self.assertRaisesRegex(Error, 'revision changed'):
                publish_session('codex', 'session', incoming, merge=True)
        self.assertEqual(json.loads(path.read_text())['revision'], '5')

    def test_cli_session_publish_and_explicit_badge_override(self):
        from codex_ui_mods.cli import main
        incoming = self.base/'incoming.json'; atomic_json(incoming, {'session': 'session', 'title': 'Fixture'})
        with patch('builtins.print'):
            self.assertEqual(main(['--home', str(self.base/'home'), 'state', 'publish-session', 'codex', 'session', '--input-file', str(incoming)]), 0)
        with patch.dict(os.environ, {'HARNESS_UI_BADGE': 'X'}):
            self.call()
        self.assertEqual(json.loads(self.path.read_text())['surfaced'][0]['badge'], 'X')
