#!/usr/bin/env python3
"""Generate seven synchronized full-source repos, Pages demos and source presets.

Only committed allowlisted blobs are exported, without Git history or user state.
No remote is configured and nothing is published.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess
import zipfile

AUTHORITY = 'https://github.com/rachael/harness-ui-mods'
TITLES = {
    'harness-ui-mods': 'Harness UI Mods',
    'codex-info-pane': 'Codex Info Pane',
    'claude-code-info-pane': 'Claude Code Info Pane',
    'codex-tui-hud-extender-statusline': 'Codex TUI HUD Extender Statusline',
    'claude-code-tui-hud-extender-statusline': 'Claude Code TUI HUD Extender Statusline',
    'harness-ui-context-drawer': 'Harness UI Context Drawer',
    'harness-ui-session-sidebar': 'Harness UI Session Sidebar',
}
ALLOW = ('.gitignore', '.claude-plugin/marketplace.json', 'LICENSE', 'NOTICE', 'LICENSES/', 'assets/', 'contracts/', 'README.md', 'pyproject.toml',
         'catalog.json', 'src/codex_ui_mods/', 'modules/', 'adapters/claude-code/', 'templates/',
         'tests/', 'tools/export_repos.py', 'site/', 'docs/guide.md')
EXPORTS = {name: ALLOW for name in TITLES}
REQUIRED = {name: ('.claude-plugin/marketplace.json', 'README.md', 'pyproject.toml', 'src/codex_ui_mods/cli.py',
                  'modules/info-pane/module.json', 'adapters/claude-code/.claude-plugin/plugin.json',
                  'LICENSES/Apache-2.0.txt', 'NOTICE', 'site/template.html', 'site/demo.js',
                  'site/style.css', 'docs/guide.md') for name in TITLES}
PRESETS = {
    'full': (None, ()),
    'codex': ('codex', ()),
    'claude': ('claude', ()),
    'info-pane': (None, ('info-pane',)),
    'context-drawer': (None, ('context-drawer',)),
    'statusline': (None, ('statusline',)),
    'session-sidebar': (None, ('session-sidebar',)),
}
ENTRYPOINTS = {
    'harness-ui-mods': (None, ()),
    'codex-info-pane': ('codex', ('info-pane',)),
    'claude-code-info-pane': ('claude', ('info-pane',)),
    'codex-tui-hud-extender-statusline': ('codex', ('statusline',)),
    'claude-code-tui-hud-extender-statusline': ('claude', ('statusline',)),
    'harness-ui-context-drawer': (None, ('context-drawer',)),
    'harness-ui-session-sidebar': (None, ('session-sidebar',)),
}
FENCE = chr(96)*3


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def bootstrap(harness=None, modules=(), all_modules=False):
    args = ['--harness', harness] if harness else []
    for module in modules:
        args += ['--module', module]
    if all_modules: args.append('--all-modules')
    return f'''#!/usr/bin/env python3
"""Generated bootstrap; implementation is maintained in harness-ui-mods."""
import shutil
import subprocess
import sys

SPEC = "git+{AUTHORITY}.git"
if shutil.which("uv"):
    subprocess.run(["uv", "tool", "install", "--force", SPEC], check=True)
    command = ["uv", "tool", "run", "--from", SPEC, "harness-ui", "install"]
elif shutil.which("pipx"):
    subprocess.run(["pipx", "install", "--force", SPEC], check=True)
    command = ["pipx", "run", "--spec", SPEC, "harness-ui", "install"]
else:
    raise SystemExit("Install uv or pipx first, then rerun this bootstrap.")
print("Component presets add to existing choices. Existing off choices are preserved; --replace-selection replaces them deliberately.")
raise SystemExit(subprocess.call(command+{args!r}+sys.argv[1:]))
'''


def readme(name):
    harness, modules = ENTRYPOINTS[name]
    install = 'harness-ui install'+(' --harness '+harness if harness else '')+''.join(' --module '+module for module in modules)
    links = ' · '.join(f'[{title}](https://github.com/rachael/{repo})' for repo, title in TITLES.items())
    return f'''# {TITLES[name]}

Unofficial community project; not affiliated with or endorsed by OpenAI or Anthropic.

A full layout for a basic setup. Extra content for a customized statusline.
For **both Codex CLI and Claude Code**, in the same download.
Full shared code is on the default branch. No feature branches or independently maintained forks.

{FENCE}sh
uv tool install git+{AUTHORITY}.git
{install}
# All mods: harness-ui install --all-modules
{FENCE}

The {chr(96)}codex-ui install{chr(96)} and {chr(96)}claude-ui install{chr(96)} aliases share this package and settings. {chr(96)}claude-ui launch{chr(96)} starts native Claude Code.

Start patched Codex with {chr(96)}harness-ui launch{chr(96)}; installation leaves the stock {chr(96)}codex{chr(96)} command intact.

**Choose only what you want:** {chr(96)}install --harness codex{chr(96)} · {chr(96)}install --harness claude{chr(96)} ·
{chr(96)}install --module info-pane{chr(96)} · {chr(96)}install --module context-drawer{chr(96)} · {chr(96)}install --module statusline{chr(96)} · {chr(96)}install --module session-sidebar{chr(96)}.
Component installs add to existing choices and repeated components report already included. Use {chr(96)}--replace-selection{chr(96)} to replace deliberately; unchecking an item in the installer removes it. Repeat {chr(96)}--module{chr(96)} to combine. {chr(96)}--all-modules{chr(96)} is explicit; {chr(96)}--dry-run{chr(96)} prints a plan without changes.

The optional shared session sidebar and its associated session sharing are opt-in and off by default. It shows machine-local participating session status; it does not send sessions over a network. Other selected UI features can publish their own local state independently.

Native Claude marketplace route: {chr(96)}claude plugin marketplace add rachael/harness-ui-mods{chr(96)}, then {chr(96)}claude plugin install info-pane@harness-ui-mods{chr(96)}.

[Interactive demo and individual downloads](https://rachael.github.io/{name}/) ·
[Full source ZIP](https://github.com/rachael/{name}/archive/refs/heads/main.zip) ·
[Detailed guide](docs/guide.md) · [Verified binary releases]({AUTHORITY}/releases)

Defaults detect installed harnesses and ask before future updates. Reruns preserve choices,
explicit disables, custom status lines and pets. New optional features produce one dismissible
notice with remind-later/suppress controls; they are not automatically enabled.

Verified first-party compiled bundles can prepare supported subsets without Cargo.
Other patch combinations may require a 20–30 minute or longer source build.
Selection changes take effect after verification/update and a new launch.

<img src="site/native/pinned-line.png" width="420" alt="Native rendered pinned prompt line"> <img src="site/native/hover-drawer.png" width="420" alt="Native rendered prompt drawer">

Native rendered views from terminal-cell fixtures, not desktop screenshots.
The Pages interaction is separately labeled a synthetic simulation.

Authoritative implementation: [Harness UI Mods]({AUTHORITY}).
This repository is generated by the shared exporter. {chr(96)}SOURCE.json{chr(96)} records source commit
and file hashes; submit changes to the authoritative repository and regenerate.

{links}

MIT patcher/adapter code; Apache-2.0-derived Codex changes retain upstream license/NOTICE.
See LICENSE, NOTICE and LICENSES. No personal state is included.
'''


def render_site(target, name):
    source = target/'site'
    template = (source/'template.html').read_text()
    this_mod = '<a href="downloads/harness-ui-this-mod-source.zip">This mod only</a>' if name != 'harness-ui-mods' else ''
    harness, modules = ENTRYPOINTS[name]
    install = 'harness-ui install'+(' --harness '+harness if harness else '')+''.join(' --module '+module for module in modules)
    page = template.replace('{{MOD_LINK}}', this_mod).replace('{{INSTALL_COMMAND}}', html.escape(install)).replace('{{TITLE}}', html.escape(TITLES[name])).replace(
        '{{REPOSITORY}}', 'https://github.com/rachael/'+name)
    docs = target/'docs'; docs.mkdir(exist_ok=True)
    (docs/'index.html').write_text(page)
    for filename in ('style.css', 'demo.js'):
        (docs/filename).write_bytes((source/filename).read_bytes())
    native = source/'native'
    if not native.is_dir():
        raise ValueError('native rendered screenshot assets are missing')
    (docs/'native').mkdir(exist_ok=True)
    for path in native.iterdir():
        if path.is_file():
            (docs/'native'/path.name).write_bytes(path.read_bytes())
    (docs/'.nojekyll').write_text('')
    if (source/'pages.yml').is_file():
        workflow = target/'.github/workflows/pages.yml'; workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_bytes((source/'pages.yml').read_bytes())


def source_archives(target):
    """Presets contain the full shared source and a scoped installer bootstrap."""
    files = [(p.relative_to(target).as_posix(), p.read_bytes()) for p in sorted(target.rglob('*'))
             if p.is_file() and not p.relative_to(target).as_posix().startswith('docs/downloads/')]
    result = {}
    presets = dict(PRESETS)
    repository = json.loads((target/'SOURCE.json').read_text())['repository']
    if repository != 'harness-ui-mods': presets['this-mod'] = ENTRYPOINTS[repository]
    for preset, (harness, modules) in presets.items():
        install_script = bootstrap(harness, modules, all_modules=False)
        import io
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                if name != 'install.py':
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    if name == 'SOURCE.json':
                        provenance = json.loads(data)
                        provenance['preset'] = preset
                        provenance['files']['install.py'] = hashlib.sha256(install_script.encode()).hexdigest()
                        data = (json.dumps(provenance, indent=2)+'\n').encode()
                    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
            info = zipfile.ZipInfo('install.py', date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, install_script.encode(), compress_type=zipfile.ZIP_DEFLATED)
        result['harness-ui-'+preset+'-source.zip'] = stream.getvalue()
    return result


def export(root, output, revision='HEAD', variants=None):
    root, output = Path(root), Path(output)
    names = list(EXPORTS) if variants is None else list(variants)
    if any(name not in EXPORTS for name in names):
        raise ValueError('unknown export variant')
    commit = git(root, 'rev-parse', '--verify', revision+'^{commit}').decode().strip()
    entries = []
    for entry in git(root, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if not entry: continue
        header, raw_path = entry.split(b'\t', 1)
        mode, kind, identity = header.decode().split()
        path = raw_path.decode('utf-8')
        if Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('unsafe source path')
        entries.append((path, mode, kind, identity))
    planned = {}
    for name in names:
        selected = [entry for entry in entries if any(entry[0] == prefix or (prefix.endswith('/') and entry[0].startswith(prefix)) for prefix in EXPORTS[name])]
        found = {entry[0] for entry in selected}
        if any(path not in found for path in REQUIRED[name]):
            raise ValueError(f'{name}: required reviewed source/license files missing')
        if any(mode not in ('100644', '100755') or kind != 'blob' for _, mode, kind, _ in selected):
            raise ValueError('exports refuse symlinks, special files and submodules')
        planned[name] = selected
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for name, selected in planned.items():
        target = output/name; target.mkdir()
        for relative, mode, _, identity in selected:
            path = target/relative; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(git(root, 'cat-file', 'blob', identity))
            path.chmod(0o755 if mode == '100755' else 0o644)
        (target/'README.md').write_text(readme(name))
        (target/'install.py').write_text(bootstrap(*ENTRYPOINTS[name]))
        (target/'install.py').chmod(0o755)
        render_site(target, name)
        hashes = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(target.rglob('*')) if p.is_file()}
        report = {'schema': 1, 'repository': name, 'authoritative_source': AUTHORITY,
                  'source_commit': commit, 'files': hashes}
        (target/'SOURCE.json').write_text(json.dumps(report, indent=2)+'\n')
        downloads = target/'docs/downloads'; downloads.mkdir()
        sums = []
        for filename, data in source_archives(target).items():
            (downloads/filename).write_bytes(data)
            sums.append(hashlib.sha256(data).hexdigest()+'  '+filename)
        (downloads/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
        reports.append({'repository': name, 'files': len(hashes), 'source_commit': commit})
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True, help='New directory; never overwrite an old export')
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--variant', action='append', choices=list(EXPORTS))
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.source, args.output, args.revision, args.variant), indent=2))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error)+'\n')


if __name__ == '__main__': main()
