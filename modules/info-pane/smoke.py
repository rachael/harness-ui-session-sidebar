"""Opt-in fullscreen native pane smoke check using isolated canonical UI state.

python3 modules/info-pane/smoke.py --run --codex /path/to/patched/codex --output /new/captures
No model call or credentials: a synthetic SessionStart hook publishes a fixture and
stops the turn; the sole configured provider is unauthenticated localhost:9.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

import re
ANSI = re.compile(rb'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()][A-Z0-9]|\x1b[@-Z\\-_]')

MARKERS = ('SharedUI-Q7', 'RoleQ7', 'TaskQ7')


def capture(argv, work, env, width):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 50, width, 0, 0))
    child = subprocess.Popen(argv, cwd=work, env=env, stdin=slave, stdout=slave,
                             stderr=slave, start_new_session=True)
    os.close(slave)
    raw = b''
    typed = None
    submitted = False
    try:
        deadline = time.monotonic()+25
        while time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                raw += chunk
                if b'\x1b[6n' in chunk:
                    os.write(master, b'\x1b[1;1R')
                if b'\x1b[c' in chunk or b'\x1b[0c' in chunk:
                    os.write(master, b'\x1b[?1;2c')
            if typed is None and b'Worked for' in raw:
                os.write(master, b'/exit')
                typed = time.monotonic()
            if typed is not None and not submitted and time.monotonic()-typed > .3:
                os.write(master, b'\r')
                submitted = True
            if child.poll() is not None:
                break
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        return raw, child.returncode
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        os.close(master)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--output', type=Path, help='New evidence directory')
    args = parser.parse_args()
    if not args.run or not args.codex:
        parser.error('--run and an installed patched Codex are required')
    if args.output is None:
        args.output = Path(tempfile.mkdtemp(prefix='info-pane-smoke-'))/'captures'
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {'codex': str(Path(args.codex).resolve()), 'cases': [], 'passed': False}
    with tempfile.TemporaryDirectory(prefix='codex-shared-state-') as temporary:
        base = Path(temporary)
        for label, width, enabled in (('wide', 160, True), ('narrow', 120, True), ('disabled', 160, False)):
            case = base/label
            home, work = case/'codex', case/'work'
            home.mkdir(parents=True); work.mkdir()
            receipt = case/'hook-receipt.json'
            hook = case/'hook.py'
            hook.write_text(
                'import json,sys,time,pathlib,os\n'
                'p=json.load(sys.stdin);sid=p["session_id"];root=pathlib.Path(os.environ["CODEX_UI_STATE_DIR"])\n'
                'data={"harness":"Codex","session":sid,"title":"SharedUI-Q7","views":{"main":{"answer":"Fixture answer","tasks":[{"id":"q7","subject":"TaskQ7","status":"in_progress"}]}}}\n'
                'section={"fixture":{"title":"Example","rows":[{"label":"Role","value":"RoleQ7"}]}}\n'
                'for rel,value in [("sessions/codex/"+sid+".json",data),("sections/"+sid+".json",section)]:\n'
                ' f=root/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(value))\n'
                f'pathlib.Path({str(receipt)!r}).write_text(json.dumps({{"session_id":sid,"published":True}}))\n'
                'print(json.dumps({"continue":False}))\n')
            (home/'hooks.json').write_text(json.dumps({'hooks': {'SessionStart': [{'hooks': [
                {'type': 'command', 'command': shlex.join([sys.executable, str(hook)]), 'timeout': 10}]}]}}))
            (home/'config.toml').write_text(
                'model="gpt-6"\nmodel_provider="fixture"\n'
                '[model_providers.fixture]\nname="fixture"\nbase_url="http://127.0.0.1:9/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n[features]\nhooks=true\n'
                f'[projects.{json.dumps(str(work))}]\ntrust_level="trusted"\n')
            env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(case),
                   'TERM': 'xterm-256color', 'SHELL': '/bin/sh', 'CODEX_HOME': str(home),
                   'CODEX_SQLITE_HOME': str(home), 'CODEX_UI_INFO_PANE': '1' if enabled else '0',
                   'XDG_CONFIG_HOME': str(case/'config'), 'CODEX_UI_STATE_DIR': str(case/'state')}
            # This bypass applies only to the synthetic temporary hook above.
            raw, code = capture([args.codex, '--dangerously-bypass-hook-trust', 'fixture startup'], work, env, width)
            plain = ANSI.sub(b'', raw).decode(errors='replace')
            (args.output/f'{label}.ansi').write_bytes(raw)
            (args.output/f'{label}.txt').write_text(plain)
            visible = {marker: marker in plain for marker in MARKERS}
            expected = enabled and width == 160
            detail = {'case': label, 'width': width, 'enabled': enabled, 'returncode': code,
                      'hook': json.loads(receipt.read_text()) if receipt.exists() else None,
                      'markers': visible, 'fullscreen': b'\x1b[?1049h' in raw}
            detail['passed'] = bool(code == 0 and detail['hook'] and detail['fullscreen'] and
                                    all(found == expected for found in visible.values()))
            evidence['cases'].append(detail)
            (args.output/f'{label}.json').write_text(json.dumps(detail, indent=2)+'\n')
            print(json.dumps(detail), flush=True)
    evidence['passed'] = all(case['passed'] for case in evidence['cases'])
    (args.output/'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n')
    return 0 if evidence['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
