"""Exercise the actual patched Codex binary in an isolated fullscreen PTY.

No model request is submitted. Resume history is synthetic; the only configured
provider is unauthenticated localhost:9. --prompt-file is opt-in capture content.
"""
import argparse
import codecs
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unicodedata
import uuid


class Screen:
    """Small VT screen reader for ratatui's cursor/erase output, not an ANSI stripper."""
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.grid = [[' '] * width for _ in range(height)]
        self.x = self.y = 0
        self.saved = (0, 0)
        self.pending = ''
        self.decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def text(self):
        return '\n'.join(''.join(row).rstrip() for row in self.grid)

    def feed(self, data):
        self.pending += self.decoder.decode(data)
        while self.pending:
            value = self.pending
            if value.startswith('\x1b['):
                match = re.match(r'\x1b\[([0-?]*)([ -/]*)([@-~])', value)
                if not match:
                    return
                self.pending = value[match.end():]
                self.csi(match[1], match[3])
            elif value.startswith(('\x1b]', '\x1bP', '\x1b_', '\x1b^')):
                match = re.search(r'\x07|\x1b\\', value[2:])
                if not match:
                    return
                self.pending = value[2 + match.end():]
            elif value.startswith('\x1b'):
                if len(value) < 2:
                    return
                self.pending = value[2:]
                if value[1] == '7': self.saved = (self.x, self.y)
                elif value[1] == '8': self.x, self.y = self.saved
                elif value[1] in '()' and self.pending: self.pending = self.pending[1:]
            else:
                character, self.pending = value[0], value[1:]
                if character == '\r': self.x = 0
                elif character in '\n\v\f': self.linefeed()
                elif character == '\b': self.x = max(0, self.x - 1)
                elif character == '\t': self.x = min(self.width - 1, (self.x // 8 + 1) * 8)
                elif not unicodedata.category(character).startswith('C'):
                    if unicodedata.combining(character):
                        if self.x: self.grid[self.y][min(self.x - 1, self.width - 1)] += character
                        continue
                    width = 2 if unicodedata.east_asian_width(character) in 'WF' else 1
                    if self.x + width > self.width:
                        self.x = 0
                        self.linefeed()
                    self.grid[self.y][self.x] = character
                    if width == 2: self.grid[self.y][self.x + 1] = ''
                    self.x += width

    def linefeed(self):
        self.y += 1
        if self.y >= self.height:
            self.grid.pop(0)
            self.grid.append([' '] * self.width)
            self.y = self.height - 1

    def csi(self, raw, command):
        private = raw.startswith('?')
        values = [int(x) if x else 0 for x in raw.lstrip('?=>').split(';') if x.isdigit() or not x]
        n = (values[0] if values else 0) or 1
        if command in 'Hf':
            self.y = min(self.height - 1, max(0, n - 1))
            self.x = min(self.width - 1, max(0, (values[1] if len(values) > 1 else 1) - 1))
        elif command == 'A': self.y = max(0, self.y - n)
        elif command == 'B': self.y = min(self.height - 1, self.y + n)
        elif command == 'C': self.x = min(self.width - 1, self.x + n)
        elif command == 'D': self.x = max(0, self.x - n)
        elif command == 'G': self.x = min(self.width - 1, n - 1)
        elif command == 'd': self.y = min(self.height - 1, n - 1)
        elif command == 'E': self.y, self.x = min(self.height - 1, self.y + n), 0
        elif command == 'F': self.y, self.x = max(0, self.y - n), 0
        elif command == 'J':
            mode = values[0] if values else 0
            if mode in (2, 3): self.grid = [[' '] * self.width for _ in range(self.height)]
            elif mode == 0:
                self.grid[self.y][self.x:] = [' '] * (self.width - self.x)
                for y in range(self.y + 1, self.height): self.grid[y] = [' '] * self.width
            elif mode == 1:
                for y in range(self.y): self.grid[y] = [' '] * self.width
                self.grid[self.y][:self.x + 1] = [' '] * (self.x + 1)
        elif command == 'K':
            mode = values[0] if values else 0
            start, end = (0, self.width) if mode == 2 else ((0, self.x + 1) if mode == 1 else (self.x, self.width))
            self.grid[self.y][start:end] = [' '] * (end - start)
        elif command == 'X': self.grid[self.y][self.x:min(self.width, self.x + n)] = [' '] * min(n, self.width - self.x)
        elif command == 's': self.saved = (self.x, self.y)
        elif command == 'u': self.x, self.y = self.saved
        elif command == 'h' and private and 1049 in values:
            self.grid = [[' '] * self.width for _ in range(self.height)]
            self.x = self.y = 0


class Terminal:
    def __init__(self, argv, work, env, width=100, height=28):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
        self.screen = Screen(width, height)
        self.raw = bytearray()
        self.child = subprocess.Popen(argv, cwd=work, env=env, stdin=slave, stdout=slave,
                                      stderr=slave, start_new_session=True)
        os.close(slave)

    def send(self, value):
        os.write(self.master, value)

    def pump(self, seconds=.4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([self.master], [], [], min(.05, max(0, end-time.monotonic())))[0]:
                try: chunk = os.read(self.master, 65536)
                except OSError: break
                if not chunk: break
                self.raw.extend(chunk)
                self.screen.feed(chunk)
                if b'\x1b[6n' in chunk: self.send(b'\x1b[1;1R')
                if b'\x1b[c' in chunk or b'\x1b[0c' in chunk: self.send(b'\x1b[?1;2c')
                if b'\x1b[18t' in chunk: self.send(f'\x1b[8;{self.screen.height};{self.screen.width}t'.encode())
        return self.screen.text()

    def wait(self, predicate, label, timeout=35):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = self.pump(.15)
            if predicate(text): return text
            if self.child.poll() is not None: break
        raise AssertionError(f'{label} failed; last screen:\n{self.screen.text()}')

    def mouse(self, button, x, y, release=False):
        self.send(f'\x1b[<{button};{x+1};{y+1}{"m" if release else "M"}'.encode())
        return self.pump()

    def resize(self, width, height):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
        self.screen = Screen(width, height)
        os.killpg(self.child.pid, signal.SIGWINCH)
        self.pump(.7)

    def close(self):
        if self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGTERM)
            try: self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait()
        os.close(self.master)


def seed(home, work, prompt):
    session = str(uuid.uuid4())
    root = home/'sessions/2026/01/01'
    root.mkdir(parents=True)
    path = root/f'rollout-2026-01-01T00-00-00-{session}.jsonl'
    def record(kind, payload):
        return {'timestamp':'2026-01-01T00:00:00.000Z', 'type':kind, 'payload':payload}
    records = [record('session_meta', {'id':session, 'timestamp':'2026-01-01T00:00:00Z',
        'cwd':str(work), 'originator':'drawer-smoke', 'cli_version':'0.164.0',
        'source':'cli', 'model_provider':'fixture'})]
    for role, value, kind in [('user',prompt,'input_text'),('assistant','\n'.join(f'ANSWER-{i:03} retained synthetic response line' for i in range(80)),'output_text')]:
        records.append(record('response_item', {'type':'message', 'id':None, 'role':role,
            'content':[{'type':kind,'text':value}], 'phase':'final_answer' if role == 'assistant' else None}))
    # Response items retain model context; native event history drives TUI replay.
    turn = str(uuid.uuid4())
    answer = '\n'.join(f'ANSWER-{i:03} retained synthetic response line' for i in range(80))
    for event in [
        {'type':'task_started','turn_id':turn,'model_context_window':None},
        {'type':'user_message','message':prompt},
        {'type':'agent_message','message':answer,'phase':'final_answer'},
        {'type':'task_complete','turn_id':turn,'last_agent_message':answer},
    ]:
        records.append(record('event_msg', event))
    path.write_text(''.join(json.dumps(record)+'\n' for record in records))
    (home/'config.toml').write_text('model="fixture-model"\nmodel_provider="fixture"\ncheck_for_update_on_startup=false\n'
        '[model_providers.fixture]\nname="fixture"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\nrequires_openai_auth=false\n'
        f'[projects.{json.dumps(str(work))}]\ntrust_level="trusted"\n')
    return session


def run_case(binary, root, output, prompt, enabled):
    case = root/('enabled' if enabled else 'disabled')
    home, work = case/'home', case/'work'
    home.mkdir(parents=True); work.mkdir()
    session = seed(home, work, prompt)
    env = {'PATH':os.environ.get('PATH','/usr/bin:/bin'), 'HOME':str(case), 'CODEX_HOME':str(home),
           'CODEX_SQLITE_HOME':str(home), 'XDG_CONFIG_HOME':str(case/'config'),
           'TERM':'xterm-256color', 'SHELL':'/bin/sh', 'CODEX_UI_CONTEXT_DRAWER':'1' if enabled else '0','CODEX_UI_INFO_PANE':'0','CODEX_UI_STATUS_LINE':'0'}
    terminal = Terminal([binary,'--no-daemon','resume',session], work, env)
    captures = {}
    def capture(label):
        captures[label] = terminal.screen.text()
        (output/f'{"enabled" if enabled else "disabled"}-{label}.txt').write_text(captures[label])
    try:
        terminal.wait(lambda text:'ANSWER-079' in text, 'resumed retained answer')
        terminal.send(b'DRAWER_DRAFT_KEEP')
        baseline = terminal.wait(lambda text:'DRAWER_DRAFT_KEEP' in text, 'composer draft')
        header_prefix = ' '.join(prompt.split())[:35]
        header_y = next((y for y,line in enumerate(baseline.splitlines()) if header_prefix in line), None)
        assert header_y is not None, 'Pinned context strip not visible in resumed session'
        composer_y = next(y for y,line in enumerate(baseline.splitlines()) if 'DRAWER_DRAFT_KEEP' in line)
        capture('collapsed')
        terminal.mouse(35, 4, header_y)
        if not enabled:
            assert 'enter jump' not in terminal.screen.text(), 'Disabled drawer captured hover'
            assert terminal.screen.text().splitlines()[:composer_y] == baseline.splitlines()[:composer_y], 'Disabled hover changed viewport'
            return {'enabled':False,'passed':True,'checks':['native resume','disabled hover']}
        terminal.wait(lambda text:'enter jump' in text, 'hover expansion')
        capture('expanded')
        assert 'DRAWER_DRAFT_KEEP' in terminal.screen.text().splitlines()[composer_y], 'Hover moved composer'
        # Overflow wheel is consumed by the drawer, regardless of transcript length.
        terminal.send((f'\x1b[<65;5;{header_y+3}M'.encode()) * 80)
        terminal.pump(1.0)
        capture('overflow-bottom')
        if 'DRAWER_END_MARKER' in prompt:
            assert 'DRAWER_END_MARKER' in terminal.screen.text(), 'Drawer cannot reach full prompt ending'
        assert 'DRAWER_DRAFT_KEEP' in terminal.screen.text().splitlines()[composer_y], 'Wheel moved composer'
        terminal.mouse(35, terminal.screen.width-1, terminal.screen.height-1)
        restored = terminal.wait(lambda text:'enter jump' not in text and 'ANSWER-079' in text, 'leave collapse without navigation')
        assert restored.splitlines()[:composer_y] == baseline.splitlines()[:composer_y], 'Hover/wheel changed retained transcript viewport'
        terminal.send(b'\x1b[15~')  # F5, no prompt submission
        terminal.wait(lambda text:'enter jump' in text, 'F5 keyboard expansion')
        terminal.send(b'\x1b')
        terminal.wait(lambda text:'enter jump' not in text, 'Escape collapse')
        terminal.mouse(35,4,header_y)
        terminal.wait(lambda text:'enter jump' in text,'second hover')
        terminal.mouse(0,4,header_y)
        terminal.mouse(0,4,header_y,release=True)
        jumped = terminal.wait(lambda text:'ANSWER-079' not in text and header_prefix in text,'click navigates to original prompt')
        capture('clicked-source')
        assert 'DRAWER_DRAFT_KEEP' in jumped.splitlines()[composer_y], 'Jump changed composer'
        terminal.resize(15,28)
        assert terminal.child.poll() is None, 'Narrow resize terminated TUI'
        terminal.resize(100,28)
        capture('resized')
        return {'enabled':True,'passed':True,'checks':['native resume','hover','composer preservation','independent overflow wheel','leave restores viewport','F5','Escape','click jump','narrow resize']}
    finally:
        (output/f'{"enabled" if enabled else "disabled"}.ansi').write_bytes(terminal.raw)
        terminal.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--prompt-file',type=Path)
    args=parser.parse_args()
    binary=str(Path(args.binary).resolve())
    output=args.output or Path(tempfile.mkdtemp(prefix='drawer-smoke-evidence-'))
    output.mkdir(parents=True,exist_ok=True)
    prompt=args.prompt_file.read_text() if args.prompt_file else 'DRAWER_START_MARKER fictional orchard design request\n\n'+'\n'.join(f'Fictional design constraint {i:02}: preserve the gate and the reading position.' for i in range(35))+'\nDRAWER_END_MARKER'
    with open(binary, 'rb') as stream:
        binary_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    evidence={'binary':binary,'sha256':binary_hash,'source':'Actual binary fullscreen PTY over synthetic retained history; no inference submitted','cases':[],'passed':False}
    try:
        with tempfile.TemporaryDirectory(prefix='drawer-native-smoke-') as temporary:
            for enabled in (False,True): evidence['cases'].append(run_case(binary,Path(temporary),output,prompt,enabled))
        evidence['passed']=True
    except (AssertionError,OSError,subprocess.SubprocessError) as error:
        evidence['error']=str(error)
    (output/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))
    return 0 if evidence['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
