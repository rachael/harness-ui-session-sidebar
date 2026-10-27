"""Managed plugin copies with receipts, backups and refusal of unknown edits."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time

from .core import Error, atomic_json, digest

RECEIPT = '.harness-ui-install.json'


def files(root):
    result = {}
    def failed(error): raise error
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(d for d in dirs if d not in ('.git', '__pycache__', '.pytest_cache'))
        for name in [*dirs, *names]:
            item = Path(directory)/name
            if item.is_symlink():
                raise Error('plugin trees must not contain symlinks')
        for name in sorted(names):
            path = Path(directory)/name
            relative = str(path.relative_to(root))
            if relative == RECEIPT:
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise Error('plugin trees require regular files')
            result[relative] = digest(path)
    return result


def install_tree(source, destination, adopt_matching=False):
    source, destination = Path(source).resolve(), Path(destination).expanduser().absolute()
    if not source.is_dir() or destination.is_symlink() or source == destination.resolve() or source.is_relative_to(destination.resolve()) or destination.resolve().is_relative_to(source):
        raise Error('invalid or overlapping plugin source/destination')
    manifest_path = source/'.claude-plugin/plugin.json'
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as e:
        raise Error('source must contain .claude-plugin/plugin.json') from e
    if not isinstance(manifest, dict) or not isinstance(manifest.get('name'), str) or 'info-pane' not in manifest['name'] or not isinstance(manifest.get('version'), str):
        raise Error('only an explicitly named info-pane plugin can be installed')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_name(destination.name+'.install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise Error('another plugin install is running') from e
        wanted = files(source)
        source_hash = hashlib.sha256(json.dumps(wanted, sort_keys=True).encode()).hexdigest()
        if destination.exists():
            try:
                receipt = json.loads((destination/RECEIPT).read_text())
            except (OSError, ValueError) as e:
                if not adopt_matching or files(destination) != wanted:
                    raise Error('existing destination is not managed; only an exact matching baseline can be explicitly adopted') from e
                receipt = {'files': wanted, 'source_hash': None}
            if not isinstance(receipt, dict) or not isinstance(receipt.get('files'), dict) or files(destination) != receipt['files']:
                raise Error('installed plugin has unknown edits; preserve/review them before update')
            if receipt.get('source_hash') == source_hash:
                return {'status': 'unchanged', 'destination': str(destination), 'source_hash': source_hash}
        stage = Path(tempfile.mkdtemp(prefix='.harness-ui-stage-', dir=destination.parent))
        try:
            for relative in wanted:
                target = stage/relative; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source/relative, target)
            if files(stage) != wanted or files(source) != wanted:
                raise Error('plugin source changed while copying; not installed')
            atomic_json(stage/RECEIPT, {'schema': 1, 'name': manifest['name'], 'version': manifest['version'],
                                      'source_hash': source_hash, 'files': wanted})
            backup = None
            if destination.exists():
                backup = destination.with_name(destination.name+'.before-harness-ui-'+str(time.time_ns()))
                os.rename(destination, backup)
            try:
                os.rename(stage, destination)
            except OSError:
                if backup is not None and not destination.exists(): os.rename(backup, destination)
                raise
            return {'status': 'installed', 'destination': str(destination), 'source_hash': source_hash,
                    'backup': str(backup) if backup else None, 'plugin_name': manifest['name']}
        finally:
            if stage.exists(): shutil.rmtree(stage)
