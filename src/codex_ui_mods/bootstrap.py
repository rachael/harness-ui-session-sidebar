"""Dispatch installed entrypoints to an explicitly approved local controller.

This module imports only the standard library. Dispatch never fetches source,
changes a checkout, prompts, or imports managed code before verifying approval.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

RECEIPT = 'controller-approval.json'
MARKER = 'HARNESS_UI_DISPATCHED'
MODULES = {'codex_ui_mods.cli', 'codex_ui_mods.codex'}


class ApprovalError(RuntimeError):
    pass


def _git(catalog, *arguments):
    try:
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', f'core.hooksPath={os.devnull}', '-C', str(catalog), *arguments],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ApprovalError('approved controller Git verification could not complete') from error
    if result.returncode:
        raise ApprovalError('approved controller Git verification failed')
    return result.stdout.strip()


def _clean_commit(catalog, commit, remote, branch):
    if catalog.is_symlink() or not catalog.is_dir():
        raise ApprovalError('approved controller checkout is missing or linked')
    if _git(catalog, 'rev-parse', 'HEAD') != commit:
        raise ApprovalError('approved controller commit differs')
    if _git(catalog, 'remote', 'get-url', 'origin') != remote:
        raise ApprovalError('approved controller origin differs')
    if _git(catalog, 'branch', '--show-current') != branch:
        raise ApprovalError('approved controller branch differs')
    if _git(catalog, 'status', '--porcelain', '--untracked-files=all'):
        raise ApprovalError('approved controller checkout has local edits')


def approve(home, commit, remote, branch):
    """Record a successful consented refresh; callers own consent and update locking."""
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ApprovalError('invalid approved controller commit')
    if not all(isinstance(value, str) and value for value in (remote, branch)):
        raise ApprovalError('invalid approved controller origin or branch')
    home = Path(home).expanduser()
    _clean_commit(home/'catalog', commit, remote, branch)
    record = {'schema': 1, 'commit': commit, 'remote': remote, 'branch': branch}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=home, prefix='.controller-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, sort_keys=True); stream.write('\n')
        os.replace(temporary, home/RECEIPT)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()
    return record


def home_path(argv=None, home=None):
    if home is not None: return Path(home).expanduser()
    arguments = sys.argv[1:] if argv is None else argv
    explicit = None
    index = 0
    while index < len(arguments):
        value = arguments[index]
        if value == '--home' and index+1 < len(arguments):
            explicit = arguments[index+1]; index += 2; continue
        if value.startswith('--home='): explicit = value.partition('=')[2]
        elif value == '--' or not value.startswith('-'): break
        index += 1
    configured = os.environ.get('HARNESS_UI_HOME', os.environ.get('CODEX_UI_HOME', Path.home()/'.local/share/harness-ui-mods'))
    return Path(explicit if explicit is not None else configured).expanduser()


def _approved_source(home, module='codex_ui_mods.cli'):
    """Return a clean approved source root, or None; no source code is imported."""
    try:
        if module not in MODULES: return None
        home = Path(home).expanduser()
        record = json.loads((home/RECEIPT).read_text())
        config = json.loads((home/'config.json').read_text())
        if not isinstance(record, dict) or not isinstance(config, dict) or (type(record.get('schema')) is not int or record['schema'] != 1): return None
        commit, remote, branch = (record.get(key) for key in ('commit', 'remote', 'branch'))
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit): return None
        if not all(isinstance(value, str) and value for value in (remote, branch)): return None
        if config.get('catalog_remote') != remote or config.get('catalog_branch') != branch: return None
        catalog = home/'catalog'
        _clean_commit(catalog, commit, remote, branch)
        source = catalog/'src'
        package = source/'codex_ui_mods'
        target = package/(module.rsplit('.', 1)[1]+'.py')
        if any(path.is_symlink() for path in (source, package, package/'__init__.py', target)): return None
        if not (package/'__init__.py').is_file() or not target.is_file(): return None
        return source.resolve(), record
    except (OSError, ValueError, TypeError, ApprovalError, subprocess.SubprocessError):
        return None


def approved_source(home, module='codex_ui_mods.cli'):
    verified = _approved_source(home, module)
    return verified[0] if verified else None


def dispatch(module='codex_ui_mods.cli', argv=None, default_harness=None, home=None):
    """Re-exec a verified controller before old parser/core imports, without fetching."""
    if module not in MODULES or default_harness not in (None, 'codex', 'claude'): return False
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = home_path(arguments, home)
    verified = _approved_source(root, module)
    if verified is None: return False
    source, record = verified
    if Path(__file__).resolve().parent == source/'codex_ui_mods': return False
    marker = f"{os.getpid()}:{record['commit']}"
    if os.environ.get(MARKER) == marker: return False
    env = dict(os.environ, **{MARKER: marker})
    # sys.path is process-local: do not leak a managed PYTHONPATH into native
    # harness children, where it could bypass their own approval check later.
    code = f'import sys;sys.path.insert(0,{str(source)!r});'
    if module == 'codex_ui_mods.cli':
        code += f'from codex_ui_mods.cli import main;raise SystemExit(main(default_harness={default_harness!r}))'
    else:
        code += f'import runpy;runpy.run_module({module!r},run_name="__main__")'
    os.execve(sys.executable, [sys.executable, '-P', '-c', code, *arguments], env)
    return True


def main():
    if dispatch(): return 0
    from .cli import main as controller
    return controller()


def codex_main():
    if dispatch(default_harness='codex'): return 0
    from .cli import main as controller
    return controller(default_harness='codex')


def claude_main():
    if dispatch(default_harness='claude'): return 0
    from .cli import main as controller
    return controller(default_harness='claude')
