"""The Claude plugin's bundled state runtime, run as the plugin runs it: python3 on the
script, the default home (HARNESS_UI_HOME) and a data module that moves the state root.
Writers and the status line renderer must agree on that root."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from codex_ui_mods.core import Manager, atomic_json  # noqa: E402

RUNTIME = ROOT/'adapters'/'claude-code'/'runtime'/'harness-ui-state.py'


class BundledRuntime(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base/'home'
        self.state = self.base/'module-state'
        manager = Manager(self.home)
        atomic_json(manager.root/'modules'/'data'/'module.json', {
            'schema': 1, 'id': 'data', 'version': '1', 'description': 'Fixture data module', 'codex': ['*'], 'order': 1,
            'dependencies': [], 'patches': [], 'tests': [], 'runtime_env': {'HARNESS_UI_STATE_DIR': str(self.state)}})
        manager.config['enabled'].append('data')
        manager.save()
        self.env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(self.base), 'HARNESS_UI_HOME': str(self.home), 'NO_COLOR': '1'}

    def run_runtime(self, *argv, stdin=''):
        return subprocess.run([sys.executable, str(RUNTIME), *argv], input=stdin, capture_output=True, text=True, env=self.env, timeout=30)

    def test_default_home_module_root_is_shared_by_writer_and_renderer(self):
        paths = json.loads(self.run_runtime('state', 'paths', '--provider', 'claude').stdout)
        self.assertEqual(paths['state_root'], str(self.state))
        staged = self.base/'usage.json'
        totals = {'schema': 1, 'startedAt': 0, 'since': 0, 'input': 900, 'output': 100, 'cacheRead': 9000, 'cacheWrite': 100}
        staged.write_text(json.dumps({'session': 's1', 'usage': totals}))
        published = self.run_runtime('state', 'publish-session', 'claude', 's1', '--input-file', str(staged), '--merge', '--consume-input')
        self.assertEqual(published.returncode, 0, published.stderr)
        self.assertTrue((self.state/'sessions'/'claude'/'s1.json').exists())
        self.assertFalse(staged.exists())
        os.environ.pop('HARNESS_UI_STATUS_LINE_ITEMS', None)
        self.env['HARNESS_UI_STATUS_LINE_ITEMS'] = 'session,cache'
        line = self.run_runtime('statusline', 'render', '--provider', 'claude', stdin=json.dumps({'session_id': 's1'}))
        self.assertEqual(line.returncode, 0, line.stderr)
        self.assertEqual(line.stdout.strip(), 'sess 1K uncached+out · cache read 90%')

    def test_session_commands_go_through_the_home_publisher(self):
        # A custom --home whose data module names a publisher; the default HOME points
        # elsewhere and HARNESS_UI_HOME is unset, so nothing ambient can make this pass.
        custom = self.base/'custom-home'
        shared = self.base/'custom-state'
        log = self.base/'publisher.log'
        record = shared/'sessions'/'claude'/'s2.json'
        publisher = self.base/'publisher.py'
        publisher.write_text(
            'import json,os,pathlib,sys\n'
            f'p=pathlib.Path({str(record)!r});p.parent.mkdir(parents=True,exist_ok=True)\n'
            'old=json.loads(p.read_text()) if p.exists() else {"revision":"0"}\n'
            'assert os.environ["HARNESS_UI_EXPECT_REVISION"] == (old["revision"] if p.exists() else "")\n'
            'data=json.load(sys.stdin)\n'
            f'pathlib.Path({str(log)!r}).open("a").write(json.dumps(sorted(data))+"\\n")\n'
            'p.write_text(json.dumps({"schema_version":1,"revision":str(int(old["revision"])+1),"marker":"fixture-publisher","data":data}))\n')
        manager = Manager(custom)
        atomic_json(manager.root/'modules'/'data'/'module.json', {
            'schema': 1, 'id': 'data', 'version': '1', 'description': 'Fixture publisher', 'codex': ['*'], 'order': 1,
            'dependencies': [], 'patches': [], 'tests': [], 'runtime_env': {
                'HARNESS_UI_STATE_DIR': str(shared), 'HARNESS_UI_PUBLISH_COMMAND': json.dumps([sys.executable, str(publisher)])}})
        manager.config['enabled'] = ['data']
        manager.save()
        env = {'PATH': self.env['PATH'], 'HOME': str(self.base/'default-home'), 'TERM_PROGRAM': 'Apple_Terminal'}
        run = lambda *argv: subprocess.run([sys.executable, str(RUNTIME), '--home', str(custom), *argv],
                                           capture_output=True, text=True, env=env, timeout=30)
        announced = run('sessions', 'announce', 'claude', 's2', '--pid', str(os.getpid()))
        self.assertEqual(announced.returncode, 0, announced.stderr)
        envelope = json.loads(record.read_text())
        self.assertEqual((envelope['marker'], envelope['revision']), ('fixture-publisher', '1'))
        self.assertEqual(envelope['data']['terminal']['pid'], os.getpid())
        forgotten = run('sessions', 'forget', 'claude', 's2')
        self.assertEqual(forgotten.returncode, 0, forgotten.stderr)
        envelope = json.loads(record.read_text())
        self.assertEqual(envelope['revision'], '2')
        self.assertIsNone(envelope['data']['terminal'])
        self.assertEqual(len(log.read_text().splitlines()), 2)
        # nothing was written under the default HOME's state root
        self.assertFalse((self.base/'default-home'/'.local'/'state'/'harness-ui-mods').exists())

    def test_writes_no_bytecode_beside_the_plugin(self):
        self.run_runtime('state', 'paths', '--provider', 'claude')
        self.assertFalse(any(RUNTIME.parent.rglob('__pycache__')))


if __name__ == '__main__':
    unittest.main()
