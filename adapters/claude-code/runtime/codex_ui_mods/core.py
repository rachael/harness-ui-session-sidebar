"""Standard-library lifecycle; discovery reads data and never imports modules."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tarfile
import time
import urllib.request

REMOTE = 'https://github.com/rachael/harness-ui-mods.git'
UPSTREAM = 'https://github.com/openai/codex.git'
ID = re.compile(r'^[a-z][a-z0-9-]{0,63}$')
ENV = re.compile(r'^[A-Z_][A-Z0-9_]*$')
# Only these native modules have reviewed, tested inactive paths. Custom modules
# still require an exact patch set; a manifest cannot grant itself this capability.
RUNTIME_SWITCHES = {'info-pane': 'CODEX_UI_INFO_PANE',
                    'context-drawer': 'CODEX_UI_CONTEXT_DRAWER',
                    'statusline': 'CODEX_UI_STATUS_LINE'}


class Error(Exception):
    pass


def run(argv, cwd=None, env=None, timeout=7200):
    try:
        p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Error(str(e)) from e
    if p.returncode:
        raise Error(f'{argv[0]} exited {p.returncode}: {(p.stdout+p.stderr)[-3000:]}')
    return p.stdout


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def member(root, name):
    if not isinstance(name, str) or Path(name).is_absolute():
        raise Error('module file must be relative')
    target = (root/name).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise Error(f'module file escapes directory or is missing: {name}')
    return target


def _manifest(path):
    m = read_json(path)
    if not isinstance(m, dict) or type(m.get('schema')) is not int or m['schema'] != 1 or not isinstance(m.get('id'), str) or not ID.fullmatch(m['id']):
        raise Error(f'invalid module manifest: {path}')
    for key, typ in [('version', str), ('description', str), ('codex', list), ('patches', list),
                     ('tests', list), ('dependencies', list), ('order', int)]:
        if not isinstance(m.get(key), typ) or (typ is int and type(m[key]) is not int):
            raise Error(f'invalid module {m["id"]} field {key}')
    if not m['codex'] or not all(isinstance(v, str) for v in m['codex']):
        raise Error('codex must list exact versions or *')
    if not all(isinstance(d, str) and ID.fullmatch(d) for d in m['dependencies']):
        raise Error('invalid dependency id')
    if m.get('harness', 'codex') not in ('codex', 'claude'):
        raise Error('module harness must be codex or claude')
    for p in m['patches']:
        member(path.parent, p)
    for t in m['tests']:
        if not isinstance(t, dict) or not all(isinstance(t.get(k), str) and t[k] and not t[k].startswith('-') for k in ('package', 'filter')):
            raise Error('test requires package and nonempty filter')
    if m['patches'] and not m['tests']:
        raise Error('patch modules require filtered tests')
    if not isinstance(m.get('smoke', []), list) or not isinstance(m.get('runtime_env', {}), dict):
        raise Error('smoke must be a list and runtime_env an object')
    for command in m.get('smoke', []):
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise Error('smoke commands must be argv arrays')
    for k, v in m.get('runtime_env', {}).items():
        if not ENV.fullmatch(k) or not k.startswith(('CODEX_UI_', 'HARNESS_UI_')) or not isinstance(v, str) or '\x00' in v:
            raise Error('runtime_env accepts HARNESS_UI_* or CODEX_UI_* string values only')
    options = m.get('options', [])
    if not isinstance(options, list):
        raise Error('module options must be an array')
    seen_options = set()
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get('id'), str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', option['id']):
            raise Error('invalid module option id')
        if option['id'] in seen_options:
            raise Error('duplicate module option id')
        seen_options.add(option['id'])
        if option.get('type') == 'boolean':
            if type(option.get('default')) is not bool:
                raise Error('boolean option default must be boolean')
        elif option.get('type') == 'choice':
            choices = option.get('choices')
            if not isinstance(choices, list) or not choices or any(not isinstance(v, str) or not v or '\x00' in v for v in choices) or option.get('default') not in choices:
                raise Error('choice option requires string choices and a matching default')
        else:
            raise Error('module option type must be boolean or choice')
        key = option.get('runtime_env')
        if key is not None and (not isinstance(key, str) or not ENV.fullmatch(key) or not key.startswith(('CODEX_UI_', 'HARNESS_UI_'))):
            raise Error('module option runtime_env must be a UI environment key')
        if 'config_key' in option and (not isinstance(option['config_key'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', option['config_key'])):
            raise Error('module option config_key must name one native option')
    m = dict(m, root=str(path.parent.resolve()))
    h, code_hash = hashlib.sha256(), hashlib.sha256()
    root = path.parent.resolve()
    def failed(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(d for d in dirs if d not in ('.git', '__pycache__', '.pytest_cache'))
        for name in [*dirs, *sorted(files)]:
            item = Path(directory)/name
            if item.is_symlink():
                raise Error('module tree must not contain symlinks: '+str(item))
        for name in sorted(files):
            item = Path(directory)/name
            relative = str(item.relative_to(root)).encode()
            data = item.read_bytes()
            h.update(len(relative).to_bytes(8, 'big')); h.update(relative)
            h.update(len(data).to_bytes(8, 'big')); h.update(data)
            code = json.dumps({k: v for k, v in m.items() if k not in ('runtime_env', 'root')}, sort_keys=True).encode() if item == path.resolve() else data
            code_hash.update(len(relative).to_bytes(8, 'big')); code_hash.update(relative)
            code_hash.update(len(code).to_bytes(8, 'big')); code_hash.update(code)
    m['patch_hash'] = code_hash.hexdigest()
    m['content_hash'] = h.hexdigest()
    return m


def manifest(path):
    try:
        return _manifest(path)
    except (OSError, ValueError, TypeError) as e:
        raise Error(f'cannot read module {path}: {e}') from e


def _discover(paths):
    found = {}
    for root in paths:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for path in sorted(root.glob('*/module.json')):
            m = manifest(path)
            if m['id'] in found:
                raise Error(f'duplicate module id: {m["id"]}')
            found[m['id']] = m
    return found


def discover(paths):
    try:
        return _discover(paths)
    except (OSError, ValueError, TypeError) as e:
        raise Error('module discovery failed: '+str(e)) from e


def select(modules, enabled, version=None, disabled=()):
    chosen, visiting = [], set()
    disabled = set(disabled)
    def visit(name):
        if name in disabled:
            raise Error('an enabled module depends on an explicitly disabled module: '+name)
        if name in visiting:
            raise Error('module dependency cycle: '+name)
        if any(m['id'] == name for m in chosen):
            return
        if name not in modules:
            raise Error('missing module: '+name)
        m = modules[name]
        if version is not None and version not in m['codex'] and '*' not in m['codex']:
            raise Error(f'{name} is incompatible with Codex {version}')
        visiting.add(name)
        for dep in m['dependencies']:
            visit(dep)
        visiting.remove(name); chosen.append(m)
    for name in sorted(enabled, key=lambda n: (modules.get(n, {}).get('order', 0), n)):
        visit(name)
    return chosen


def extract_bundle(archive, destination):
    """Only four fixed regular files; never ask tar to resolve a path or link."""
    required = {'codex', 'upstream/LICENSE', 'upstream/NOTICE', 'source.json'}
    seen, total = set(), 0
    try:
        with tarfile.open(archive, 'r:gz') as tar:
            for entry in tar:
                if entry.name not in required or not entry.isfile() or entry.name in seen:
                    raise Error('unsafe or unexpected bundle entry: '+entry.name)
                total += entry.size
                if total > 2**31 or entry.size < 1:
                    raise Error('bundle exceeds size limit or has empty required file')
                seen.add(entry.name)
                target = destination/entry.name; target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(entry) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out)
    except (tarfile.TarError, OSError) as e:
        raise Error('invalid bundle: '+str(e)) from e
    if seen != required:
        raise Error('bundle missing executable, source metadata or upstream notices')


class Manager:
    def __init__(self, root=None, config_path=None):
        self.root = Path(root or os.environ.get('HARNESS_UI_HOME', os.environ.get('CODEX_UI_HOME', Path.home()/'.local/share/harness-ui-mods'))).expanduser()
        self.config_path = Path(config_path or self.root/'config.json')
        self.config = read_json(self.config_path, {
            'schema': 1, 'stock': shutil.which('codex') or 'codex', 'updates': 'off',
            'scan_paths': [str(self.root/'modules')], 'enabled': [], 'disabled': [], 'enable_new': False,
            'metadata_url': 'https://raw.githubusercontent.com/rachael/harness-ui-mods/main/catalog.json',
            'catalog_remote': REMOTE, 'catalog_branch': 'main', 'upstream': UPSTREAM,
        })

    def save(self):
        atomic_json(self.config_path, self.config)

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/'update.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise Error('another update is running') from e
            yield

    def stock_available(self):
        selected = self.config.get('stock', 'codex')
        return Path(shutil.which(selected) or selected).is_file()

    def check(self, online=False):
        claude = self.config.get('claude_install')
        if not self.stock_available() and claude:
            result = {'stock': None, 'active': False, 'claude': {'configured': True, 'destination': claude['destination']}, 'compatible': True, 'modules': []}
            if online:
                result['availability'] = self.availability({'version': '*'})
            return result
        stock = self.stock()
        try:
            selected, compatible, reason = self.selection(stock['version']), True, None
        except Error as error:
            selected, compatible, reason = [], False, str(error)
        result = {'stock': stock, 'active': bool(self.current(stock)), 'compatible': compatible, 'modules': [m['id'] for m in selected], 'reason': reason}
        if claude: result['claude'] = {'configured': True, 'destination': claude['destination']}
        if online: result['availability'] = self.availability(stock)
        return result

    def stock(self):
        path = Path(shutil.which(self.config['stock']) or self.config['stock']).resolve()
        if not path.is_file():
            raise Error('stock Codex not found; run setup --stock /path/to/codex')
        text = run([path, '--version'], timeout=20)
        match = re.search(r'\b(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b', text)
        if not match:
            raise Error('cannot determine stock Codex version')
        stock = {'path': str(path), 'version': match[1], 'sha256': digest(path)}
        candidates = [(path, path.parent/'codex-code-mode-host'),
                      (path, path.parent/'codex-resources/codex-code-mode-host')]
        machine = {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'x64', 'amd64': 'x64'}.get(platform.machine().lower())
        system = platform.system().lower()
        target = {('darwin', 'arm64'): 'aarch64-apple-darwin', ('darwin', 'x64'): 'x86_64-apple-darwin',
                  ('linux', 'arm64'): 'aarch64-unknown-linux-musl', ('linux', 'x64'): 'x86_64-unknown-linux-musl'}.get((system, machine))
        root = path.parent.parent
        if target:
            package = f'codex-{system}-{machine}'
            for package_root in (root/'node_modules/@openai'/package, root.parent/package):
                layout = package_root/'vendor'/target
                metadata = read_json(layout/'codex-package.json', {})
                if metadata.get('version') == stock['version'] and metadata.get('target') == target:
                    native = layout/'bin/codex'
                    helper = layout/'bin/codex-code-mode-host'
                    candidates.append((native, helper))
        for native, helper in candidates:
            if native.is_file() and helper.is_file():
                if native != path and not re.search(r'\b'+re.escape(stock['version'])+r'\b', run([native, '--version'], timeout=20)):
                    continue
                stock['helper'] = {'path': str(helper.resolve()), 'sha256': digest(helper)}
                stock['native'] = {'path': str(native.resolve()), 'sha256': digest(native)}
                break
        return stock

    def modules(self):
        catalog = self.root/'catalog/modules'
        return discover([catalog, *self.config['scan_paths']])

    def selection(self, version):
        all_modules = self.modules()
        modules = {name: mod for name, mod in all_modules.items() if mod.get('harness', 'codex') == 'codex'}
        enabled = list(modules) if self.config['enable_new'] else self.config['enabled']
        disabled = set(self.config.get('disabled', []))
        chosen = select(modules, [name for name in enabled if name not in disabled and (name in modules or name not in all_modules)], version, disabled)
        if any(m['id'] in disabled for m in chosen):
            raise Error('an enabled module depends on an explicitly disabled module')
        return chosen

    def module_options_environment(self, modules):
        configured = self.config.get('module_options', {})
        if not isinstance(configured, dict):
            raise Error('module_options must be an object')
        env = {}
        for module in modules:
            selected = configured.get(module['id'], {})
            if not isinstance(selected, dict):
                raise Error('module options must be an object for '+module['id'])
            declared = {option['id']: option for option in module.get('options', [])}
            if set(selected) - set(declared):
                raise Error('unknown configured option for '+module['id'])
            for name, option in declared.items():
                value = selected.get(name, option['default'])
                if option['type'] == 'boolean':
                    if type(value) is not bool:
                        raise Error('module option '+name+' must be boolean')
                    value = '1' if value else '0'
                elif not isinstance(value, str) or value not in option['choices']:
                    raise Error('invalid choice for module option '+name)
                key = option.get('runtime_env')
                if key:
                    if key in env and env[key] != value:
                        raise Error('conflicting module option environment: '+key)
                    env[key] = value
        return env

    def statusline_environment(self, modules):
        """Persisted UI choices apply only when this module is selected."""
        if not any(m['id'] == 'statusline' for m in modules):
            return {}
        options = self.config.get('statusline_options', {})
        if not isinstance(options, dict) or set(options) - {'mode', 'items', 'ascii', 'colors'}:
            raise Error('invalid statusline options')
        env = {}
        if 'mode' in options:
            if options['mode'] not in ('auto', 'extend', 'replace'):
                raise Error('statusline mode must be auto, extend or replace')
            env['HARNESS_UI_STATUS_LINE_MODE'] = options['mode']
        if 'items' in options:
            items = options['items']
            if isinstance(items, str):
                items = [part.strip() for part in items.split(',')]
            allowed = {'project', 'model', 'effort', 'tier', 'context', 'limits', 'session', 'cache', 'cost'}
            if not isinstance(items, list) or any(not isinstance(v, str) or v not in allowed for v in items):
                raise Error('unknown statusline item')
            env['HARNESS_UI_STATUS_LINE_ITEMS'] = ','.join(dict.fromkeys(items))
        for key in ('ascii', 'colors'):
            if key in options:
                if type(options[key]) is not bool:
                    raise Error('statusline '+key+' must be boolean')
                env['HARNESS_UI_STATUS_LINE_'+key.upper()] = '1' if options[key] else '0'
        return env

    def fingerprint(self, stock, modules):
        data = {'stock': stock, 'modules': [(m['id'], m['version'], m['content_hash']) for m in modules]}
        module_options = self.module_options_environment(modules)
        if module_options:
            data['module_options'] = module_options
        options = self.statusline_environment(modules)
        if options:
            data['statusline_options'] = options
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def patch_fingerprint(self, stock, modules):
        return hashlib.sha256(json.dumps({'stock': stock, 'modules': [(m['id'], m['version'], m['patch_hash']) for m in modules if m['patches']]}, sort_keys=True).encode()).hexdigest()

    def compiled_compatible(self, compiled, switches, modules, version):
        wanted = {m['id']: m['patch_hash'] for m in modules if m['patches']}
        if not isinstance(compiled, dict) or not isinstance(switches, dict):
            return False
        if not wanted or any(compiled.get(k) != v for k, v in wanted.items()):
            return False
        if not switches:
            return compiled == wanted
        # Verify the entire compiled set against the trusted local catalog, not
        # merely its selected subset. Changed/removed modules invalidate reuse.
        if switches != {name: RUNTIME_SWITCHES.get(name) for name in compiled} or any(v is None for v in switches.values()):
            return False
        available = self.modules()
        for name, sha in compiled.items():
            m = available.get(name)
            if not m or m['patch_hash'] != sha or not m['patches'] or m.get('harness', 'codex') != 'codex':
                return False
            if version not in m['codex'] and '*' not in m['codex']:
                return False
            if m.get('runtime_env', {}).get(switches[name]) != '1':
                return False
        return True

    def current(self, stock=None, runtime=True):
        stock = stock or self.stock()
        try:
            record = read_json(self.root/'current.json')
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or record.get('stock') != stock:
            return None
        try:
            modules = self.selection(stock['version'])
            binary = Path(record['binary'])
            if record.get('helper_sha256') and digest(binary.parent/'codex-code-mode-host') != record['helper_sha256']:
                return None
            for relative, expected in record.get('notices', {}).items():
                if relative not in ('upstream/LICENSE', 'upstream/NOTICE') or digest(binary.parent/relative) != expected:
                    return None
            if record.get('compiled_modules'):
                if not self.compiled_compatible(record['compiled_modules'], record.get('runtime_switches', {}), modules, stock['version']):
                    return None
            elif record.get('patch_fingerprint') != self.patch_fingerprint(stock, modules):
                return None
            if digest(binary) != record['sha256']:
                return None
            if runtime and record.get('fingerprint') != self.fingerprint(stock, modules):
                return None
        except (Error, OSError, ValueError, KeyError, TypeError):
            return None
        return record

    def availability(self, stock):
        """Download bounded JSON metadata only; no executable Git content or artifact."""
        url = self.config.get('metadata_url', '')
        if not url.startswith('https://'):
            raise Error('metadata URL must use HTTPS')
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if not response.geturl().startswith('https://'):
                    raise Error('metadata redirected away from HTTPS')
                data = response.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise Error('metadata exceeds size limit')
            metadata = json.loads(data)
        except (OSError, ValueError) as e:
            raise Error('metadata unavailable: '+str(e)) from e
        if not isinstance(metadata, dict) or type(metadata.get('schema')) is not int or metadata['schema'] != 1:
            raise Error('metadata must be a schema-1 object')
        entries = metadata.get('modules', [])
        if not isinstance(entries, list):
            raise Error('metadata modules must be an array')
        available = {}
        for module in entries:
            if not isinstance(module, dict) or not isinstance(module.get('id'), str) or not ID.fullmatch(module['id']):
                raise Error('metadata module id is invalid')
            versions = module.get('codex')
            if not isinstance(versions, list) or not versions or not all(isinstance(v, str) and v for v in versions):
                raise Error('metadata codex compatibility must be a nonempty string array')
            if module['id'] in available:
                raise Error('duplicate metadata module id')
            available[module['id']] = module
        # Data-only local extensions do not need a new native patch, and are not
        # expected to appear in a public catalog. Compatibility is checked using
        # the remote version list, since the local patches may target old stock.
        enabled = [module['id'] for module in self.selection(None) if module['patches']]
        ready = bool(enabled) and all(name in available and (stock['version'] in available[name].get('codex', []) or '*' in available[name].get('codex', [])) for name in enabled)
        return {'compatible_patch_ready': ready, 'codex': stock['version'], 'metadata_only': True}

    def refresh(self):
        """Only fast-forward the explicitly trusted remote; never reset local edits."""
        path = self.root/'catalog'
        remote, branch = self.config['catalog_remote'], self.config['catalog_branch']
        if not path.exists():
            run(['git', 'clone', '--single-branch', '--branch', branch, '--', remote, path], timeout=900)
        else:
            if run(['git', '-C', path, 'status', '--porcelain', '--untracked-files=all']).strip():
                raise Error('catalog has local edits; preserve/review before update')
            if run(['git', '-C', path, 'remote', 'get-url', 'origin']).strip() != remote:
                raise Error('catalog remote differs from trusted configuration')
            if run(['git', '-C', path, 'branch', '--show-current']).strip() != branch:
                raise Error('catalog branch differs from trusted configuration')
            run(['git', '-C', path, 'fetch', '--no-tags', 'origin', branch], timeout=900)
            run(['git', '-C', path, 'merge', '--ff-only', 'FETCH_HEAD'])
        commit = run(['git', '-C', path, 'rev-parse', 'HEAD']).strip()
        if not self.config_path.exists(): self.save()
        from .bootstrap import approve, ApprovalError
        try: approve(self.root, commit, remote, branch)
        except ApprovalError as error: raise Error(str(error)) from error
        return commit

    def build(self, stock, modules):
        """Fresh managed worktree per attempt; failed/dirty attempts are retained."""
        fingerprint = self.fingerprint(stock, modules)
        upstream = self.root/'upstream.git'
        if not upstream.exists():
            run(['git', 'clone', '--bare', '--', self.config['upstream'], upstream], timeout=1800)
        if run(['git', '--git-dir', upstream, 'remote', 'get-url', 'origin']).strip() != self.config['upstream']:
            raise Error('upstream remote differs from trusted configuration')
        tag = 'rust-v'+stock['version']
        run(['git', '--git-dir', upstream, 'fetch', '--no-tags', 'origin', 'tag', tag], timeout=900)
        attempts = self.root/'attempts'; attempts.mkdir(exist_ok=True)
        attempt = Path(tempfile.mkdtemp(prefix=stock['version']+'-', dir=attempts))
        source = attempt/'src'
        run(['git', '--git-dir', upstream, 'worktree', 'add', '--detach', source, tag], timeout=900)
        for m in modules:
            for patch in m['patches']:
                run(['git', '-C', source, 'apply', '--index', member(Path(m['root']), patch)])
        rs = source/'codex-rs'
        env = dict(os.environ, CARGO_TARGET_DIR=str(self.root/'target'), CARGO_BUILD_JOBS=os.environ.get('CARGO_BUILD_JOBS', '2'))
        for snap in (rs/'tui/src').rglob('*.snap'):
            data = snap.read_bytes()
            if b'(v0.0.0)' in data:
                snap.write_bytes(data.replace(b'(v0.0.0)', f'(v{stock["version"]})'.encode()))
        for m in modules:
            for test in m['tests']:
                text = run(['cargo', 'test', '-p', test['package'], test['filter'], '--lib'], cwd=rs, env=env)
                if not any(int(n) > 0 for n in re.findall(r'test result: ok\. (\d+) passed;', text)):
                    raise Error(f'{m["id"]}: no passing tests for {test["filter"]}')
        run(['cargo', 'build', '--release', '-p', 'codex-cli', '--bin', 'codex'], cwd=rs, env=env)
        built = self.root/'target/release/codex'
        if stock['version'] not in run([built, '--version'], timeout=20):
            raise Error('built binary version does not match stock')
        self.prepare_helper(built.parent, stock)
        self.smoke(built, modules, source, env)
        # Stock may have updated while Cargo was running: never activate that build.
        if self.stock() != stock:
            raise Error('stock changed during build; candidate retained but not activated')
        return self.activate(built, stock, modules, 'release', str(source),
                             run(['git', '-C', source, 'rev-parse', 'HEAD']).strip(),
                             {'LICENSE': source/'LICENSE', 'NOTICE': source/'NOTICE'})

    def prepare_helper(self, directory, stock):
        helper = stock.get('helper')
        modern = tuple(map(int, stock['version'].split('-')[0].split('+')[0].split('.'))) >= (0, 164, 0)
        if modern and not helper:
            raise Error('matching stock codex-code-mode-host required for this Codex version')
        if helper:
            if digest(Path(helper['path'])) != helper['sha256']:
                raise Error('stock CodeMode helper changed before activation')
            shutil.copy2(helper['path'], directory/'codex-code-mode-host')
            (directory/'codex-code-mode-host').chmod(0o755)
        return helper

    def smoke(self, binary, modules, source='', env=None):
        env = dict(os.environ if env is None else env)
        for module in self.modules().values():
            for key in module.get('runtime_env', {}):
                env.pop(key, None)
            for option in module.get('options', []):
                if option.get('runtime_env'):
                    env.pop(option['runtime_env'], None)
        env.update({flag: '0' for flag in RUNTIME_SWITCHES.values()})
        for module in modules:
            env.update({k: str(Path(v).expanduser()) if v.startswith('~') else v for k, v in module.get('runtime_env', {}).items()})
        env.update(self.module_options_environment(modules))
        env.update(self.statusline_environment(modules))
        active_ids = {m['id'] for m in modules}
        for name, flag in RUNTIME_SWITCHES.items():
            if name not in active_ids:
                env[flag] = '0'
        for m in modules:
            for argv in m.get('smoke', []):
                run([arg.replace('{binary}', str(binary)).replace('{source}', str(source)) for arg in argv], cwd=m['root'], env=env)

    def activate(self, built, stock, modules, profile, source, commit=None, notices=None, archive_sha256=None, compiled_modules=None, runtime_switches=None):
        if self.stock() != stock:
            raise Error('stock changed; candidate retained but not activated')
        reported = re.search(r'\b(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b', run([built, '--version'], timeout=20))
        if not reported or reported[1] != stock['version']:
            raise Error('candidate version does not match stock')
        compiled_modules = compiled_modules or {m['id']: m['patch_hash'] for m in modules if m['patches']}
        runtime_switches = runtime_switches or {}
        if compiled_modules and not self.compiled_compatible(compiled_modules, runtime_switches, modules, stock['version']):
            raise Error('compiled module set changed or lacks verified runtime switches')
        fingerprint = self.fingerprint(stock, modules)
        if self.fingerprint(stock, self.selection(stock['version'])) != fingerprint:
            raise Error('module selection/content changed during preparation; not activated')
        builds = self.root/'builds'; builds.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=builds))
        install = builds/(fingerprint+'-'+stage.name.removeprefix('.pending-'))
        data_only = profile == 'stock-with-data'
        binary = Path(stock['path']) if data_only else install/'codex'
        candidate = Path(stock['path']) if data_only else stage/'codex'
        if not data_only:
            shutil.copy2(built, candidate); candidate.chmod(0o755)
        helper = None if data_only else self.prepare_helper(stage, stock)
        notice_hashes = {}
        if profile == 'artifact' or any(m['patches'] for m in modules):
            if not notices or set(notices) != {'LICENSE', 'NOTICE'}:
                raise Error('patched installation requires upstream LICENSE and NOTICE')
            (stage/'upstream').mkdir()
            for name, path in notices.items():
                if not Path(path).is_file() or not Path(path).read_bytes().strip():
                    raise Error('missing upstream '+name)
                shutil.copy2(path, stage/'upstream'/name)
                if name == 'NOTICE':
                    with (stage/'upstream'/name).open('a') as out:
                        out.write('\nModified by Harness UI Mods; selected modules: '+', '.join(m['id']+' '+m['version'] for m in modules)+'\n')
                notice_hashes['upstream/'+name] = digest(stage/'upstream'/name)
        source_metadata = {'schema': 1, 'codex': stock['version'], 'upstream_commit': commit,
                           'binary_sha256': digest(candidate),
                           'modules': compiled_modules, 'runtime_switches': runtime_switches}
        atomic_json(stage/'source.json', source_metadata)
        record = {'stock': stock, 'fingerprint': fingerprint, 'binary': str(binary), 'sha256': digest(candidate),
                  'profile': profile, 'compiled_modules': compiled_modules, 'runtime_switches': runtime_switches, 'patch_fingerprint': self.patch_fingerprint(stock, modules), 'modules': [{k: m[k] for k in ('id', 'version', 'content_hash')} for m in modules],
                  'created': time.time(), 'source': source, 'upstream_commit': commit,
                  'notices': notice_hashes, 'archive_sha256': archive_sha256,
                  'helper_sha256': helper['sha256'] if helper else None}
        atomic_json(stage/'installation.json', record)
        os.rename(stage, install)
        atomic_json(self.root/'current.json', record)
        return record

    def artifact(self, stock, modules):
        catalog = read_json(self.root/'catalog/catalog.json', {})
        target = platform.system().lower()+'-'+platform.machine().lower()
        for bundle in catalog.get('bundles', []):
            if bundle.get('codex') != stock['version'] or bundle.get('platform') != target:
                continue
            compiled, switches = bundle.get('modules'), bundle.get('runtime_switches', {})
            if not self.compiled_compatible(compiled, switches, modules, stock['version']):
                continue
            url, sha = bundle.get('url', ''), bundle.get('sha256', '')
            if not url.startswith('https://') or not re.fullmatch('[a-f0-9]{64}', sha):
                raise Error('artifact requires HTTPS URL and SHA256')
            downloads = self.root/'downloads'; downloads.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='bundle-', dir=downloads) as temporary:
                stage = Path(temporary)
                archive = stage/'bundle.tar.gz'
                with archive.open('wb') as out, urllib.request.urlopen(url, timeout=120) as response:
                    if not response.geturl().startswith('https://'):
                        raise Error('artifact redirected away from HTTPS')
                    total = 0
                    while data := response.read(1024*1024):
                        total += len(data)
                        if total > 2**31:
                            raise Error('artifact exceeds size limit')
                        out.write(data)
                if digest(archive) != sha:
                    raise Error('artifact SHA256 mismatch')
                extracted = stage/'extracted'; extracted.mkdir()
                extract_bundle(archive, extracted)
                metadata = read_json(extracted/'source.json')
                if not isinstance(metadata, dict) or metadata.get('schema') != 1 or metadata.get('codex') != stock['version'] or metadata.get('modules') != compiled or metadata.get('runtime_switches', {}) != switches:
                    raise Error('bundle source metadata does not match selected modules/version')
                commit = metadata.get('upstream_commit', '')
                if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40,64}', commit):
                    raise Error('bundle requires exact upstream commit')
                binary = extracted/'codex'
                if digest(binary) != metadata.get('binary_sha256'):
                    raise Error('bundle binary SHA256 mismatch')
                binary.chmod(0o755)
                self.prepare_helper(extracted, stock)
                self.smoke(binary, modules)
                return self.activate(binary, stock, modules, 'artifact', url, commit,
                                     {name: extracted/'upstream'/name for name in ('LICENSE', 'NOTICE')}, sha, compiled, switches)
        return None

    def sync_claude(self, refresh=False):
        configured = self.config.get('claude_install')
        if configured:
            from .claude import install, update_native
            command = configured.get('command', 'claude')
            result = install(Path(configured.get('source') or self.root/'catalog/adapters/claude-code'),
                             Path(configured['destination']) if configured.get('destination') else None, command,
                             ownership=configured.get('ownership', 'auto'))
            if refresh and result.get('ownership') == 'native' and result.get('status') == 'unchanged':
                expected = configured.get('plugin', 'info-pane@harness-ui-mods')
                if result.get('plugin_ref') != expected:
                    raise Error('native plugin owner changed; no update attempted')
                return update_native(command, expected)
            return result
        return None

    def update(self, yes=False, refresh=True, no_build=False, refresh_native=None, include_claude=True):
        with self.lock():
            if self.config['updates'] == 'ask' and not yes:
                if not sys_stdin_yes():
                    return {'status': 'pending', 'reason': 'trusted update fetch/install needs approval'}
                yes = True
            if refresh:
                self.refresh()
            claude = self.sync_claude(refresh=refresh if refresh_native is None else refresh_native) if include_claude else None
            if not self.stock_available() and claude is not None:
                return {'status': 'current' if claude['status'] == 'unchanged' else 'installed', 'harness': 'claude', 'installation': claude}
            stock = self.stock()
            modules = self.selection(stock['version'])
            if self.current(stock):
                return {'status': 'current'}
            if not modules:
                return {'status': 'stock', 'reason': 'no enabled modules'}
            reusable = self.current(stock, runtime=False)
            if reusable and any(m['patches'] for m in modules):
                binary = Path(reusable['binary'])
                self.smoke(binary, modules, reusable['source'] if reusable['profile'] == 'release' else '')
                notices = {name: binary.parent/'upstream'/name for name in ('LICENSE', 'NOTICE')}
                return {'status': 'runtime-refreshed', 'installation': self.activate(binary, stock, modules,
                        reusable['profile'], reusable['source'], reusable.get('upstream_commit'), notices,
                        reusable.get('archive_sha256'), reusable.get('compiled_modules'), reusable.get('runtime_switches'))}
            if not any(m['patches'] for m in modules):
                env = dict(os.environ)
                for m in modules:
                    env.update({k: str(Path(v).expanduser()) if v.startswith('~') else v for k, v in m.get('runtime_env', {}).items()})
                self.smoke(Path(stock['path']), modules, env=env)
                return {'status': 'installed', 'installation': self.activate(Path(stock['path']), stock, modules, 'stock-with-data', 'stock')}
            ready = self.artifact(stock, modules)
            if ready is None:
                if no_build:
                    raise Error('no compatible verified bundle; source build disabled, stock remains available')
                print('No compatible verified prebuilt patch set available; building from source may take 20–30 minutes or longer. Stock remains available.', file=__import__('sys').stderr)
                ready = self.build(stock, modules)
            return {'status': 'installed', 'installation': ready}

    def launch(self, argv):
        stock = self.stock()
        record = self.current(stock)
        env = dict(os.environ)
        # A selected bundle must not inherit flags from excluded modules.
        try:
            for module in self.modules().values():
                for key in module.get('runtime_env', {}):
                    env.pop(key, None)
                for option in module.get('options', []):
                    if option.get('runtime_env'):
                        env.pop(option['runtime_env'], None)
        except (Error, OSError, ValueError, TypeError, KeyError):
            record = None
        env.update({flag: '0' for flag in RUNTIME_SWITCHES.values()})
        clean_environment = dict(env)
        try:
            from .surface import resolve_state
            shared_environment = resolve_state('codex', self)['environment']
        except (Error, OSError, ValueError, TypeError, KeyError):
            record = None
            shared_environment = {}
        active_ids = set()
        if record:
            try:
                selected = self.selection(stock['version'])
                active_ids = {m['id'] for m in selected}
                for m in selected:
                    env.update({k: str(Path(v).expanduser()) if v.startswith('~') else v for k, v in m.get('runtime_env', {}).items()})
                env.update(self.module_options_environment(selected))
                env.update(self.statusline_environment(selected))
            except (Error, OSError, ValueError, TypeError, KeyError):
                record = None
                env = clean_environment
        env.update(shared_environment)
        if not record:
            active_ids = set()
        for name, flag in RUNTIME_SWITCHES.items():
            if name not in active_ids:
                env[flag] = '0'
        selected_home = str(self.root.resolve())
        env.update(HARNESS_UI_HOME=selected_home, CODEX_UI_HOME=selected_home)
        return (record['binary'] if record else stock['path']), env


def sys_stdin_yes():
    import sys
    return sys.stdin.isatty() and input('Build/download and enable this compatible patch? [y/N] ').strip().lower() in ('y', 'yes')
