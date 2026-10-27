"""Status line renderer (contracts/statusline.md).

Formats one line from what the harness reports natively: the status line payload a
harness passes on standard input, and the session totals its collector wrote to the
state root. No transcript scans, no model calls, no prices of its own.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess

# Item names, order and drop priority as the Codex status line has them
# (status_hud.rs): priority 0 is never dropped; the highest number goes first.
DEFAULT_ITEMS = ('project', 'model', 'effort', 'tier', 'context', 'limits', 'session', 'cache')
KNOWN_ITEMS = DEFAULT_ITEMS + ('cost',)
WINDOW_NAMES = {'five_hour': '5h', 'seven_day': '7d', 'spend_limit': 'spend'}
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
SAFE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
GIT_TIMEOUT = 1.0
COLORS = {'blue': 34, 'magenta': 35, 'cyan': 36, 'yellow': 33, 'red': 31, 'white': 37, 'green': 32}


def config(environ=None, items=None, ascii=None, colors=None):
    """Items, ASCII and colors: arguments first, then HARNESS_UI_STATUS_LINE_ITEMS,
    HARNESS_UI_STATUS_LINE_ASCII=1 and HARNESS_UI_STATUS_LINE_COLORS=0|1, as Codex reads
    them. Colors default on unless NO_COLOR is set. Unknown items are ignored."""
    env = os.environ if environ is None else environ
    if items is None:
        raw = env.get('HARNESS_UI_STATUS_LINE_ITEMS')
        items = [i.strip() for i in raw.split(',')] if raw is not None else list(DEFAULT_ITEMS)
    items = [i for i in dict.fromkeys(items) if i in KNOWN_ITEMS]
    if ascii is None:
        ascii = env.get('HARNESS_UI_STATUS_LINE_ASCII') == '1'
    if colors is None:
        flag = env.get('HARNESS_UI_STATUS_LINE_COLORS')
        colors = True if flag == '1' else False if flag == '0' else 'NO_COLOR' not in env
    return {'items': items, 'ascii': bool(ascii), 'colors': bool(colors)}


def number(value):
    """A finite non-negative number, else None (no reading). Zero is a reading."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float('inf'), float('-inf')) or value < 0:
        return None
    return value


def count(value):
    """Codex's compact token count: 999, 1.5K, 132K, 1.25M."""
    value = max(0, int(value))
    if value < 1000:
        return str(value)
    for limit, suffix in ((10**12, 'T'), (10**9, 'B'), (10**6, 'M'), (10**3, 'K')):
        if value >= limit:
            scaled = value / limit
            break
    decimals = 2 if scaled < 10 else 1 if scaled < 100 else 0
    text = f'{scaled:.{decimals}f}'
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text + suffix


def model_name(model_id):
    match = re.fullmatch(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?(\[.*\])?', model_id or '')
    if not match:
        return model_id or ''
    family, major, minor = match.group(1), match.group(2), match.group(3)
    return f'{family.capitalize()} {major}.{minor}' if minor else f'{family.capitalize()} {major}'


def reset_label(resets_at, now, tz):
    """' reset HH:MM' later today, else ' reset Weekday'; '' when unknown."""
    when = None
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        when = datetime.fromtimestamp(resets_at / 1000 if resets_at > 1e11 else resets_at, timezone.utc)
    elif isinstance(resets_at, str):
        try:
            when = datetime.fromisoformat(resets_at.replace('Z', '+00:00'))
        except ValueError:
            return ''
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    if when is None:
        return ''
    when, today = when.astimezone(tz), now.astimezone(tz)
    return f' reset {when:%H:%M}' if when.date() == today.date() else f' reset {DAYS[when.weekday()]}'


def _clean(value):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(value))[:160]


def _context(d, short):
    ctx = d.get('context') or {}
    tokens, window = number(ctx.get('tokens')), number(ctx.get('window'))
    if tokens is None or not window:
        return None, 0
    percent = number(ctx.get('percent'))
    percent = percent if percent is not None else tokens / window * 100
    text = f'ctx {round(percent)}%' if short else f'ctx {count(tokens)}/{count(window)} {round(percent)}%'
    return text, percent


def segments(d, cfg, now, tz):
    """(id, text, color, priority, group) per configured item that has a reading."""
    out = []
    for item in cfg['items']:
        if item == 'project':
            cwd, home = d.get('cwd') or '', d.get('home') or ''
            if not cwd:
                continue
            folder = '~' + cwd[len(home):] if home and cwd.startswith(home) else cwd
            branch = d.get('branch') or ''
            mark = '*' if branch and d.get('dirty') else ''
            out.append(('project', _clean(folder) + (f' ({_clean(branch)}{mark})' if branch else ''), 'blue', 0, 0))
        elif item == 'model' and d.get('model'):
            out.append(('model', _clean(model_name(d['model'])), 'magenta', 3, 1))
        elif item in ('effort', 'tier') and d.get(item):
            out.append((item, _clean(d[item]), 'magenta', 9, 1))
        elif item == 'context':
            text, percent = _context(d, False)
            if text:
                out.append(('context', text, 'red' if percent >= 90 else 'yellow' if percent >= 70 else 'cyan', 0, 2))
        elif item == 'limits':
            for i, w in enumerate(d.get('windows') or []):
                left = min(100, max(0, 100 - w['used']))
                name = _clean(WINDOW_NAMES.get(w['kind'], w['kind']))
                out.append((f'limits-{i}', f"{name} {round(left)}% left{reset_label(w.get('resets_at'), now, tz)}", 'yellow', 4 if i == 0 else 5, 3))
        elif item == 'session' and d.get('tokens') is not None:
            t, since = d['tokens'], d.get('counted_since')
            label = 'sess' if since is None else f'since {since.astimezone(tz):%H:%M}'
            out.append(('session', f"{label} {count(t['input'] + t['output'])} uncached+out", 'white', 6, 4))
        elif item == 'cache' and d.get('tokens') is not None:
            t = d['tokens']
            served = t['input'] + t['cache_read'] + t['cache_write']
            if served > 0:
                out.append(('cache', f"cache read {round(t['cache_read'] / served * 100)}%", 'green', 7, 4))
        elif item == 'cost' and d.get('cost') is not None:
            out.append(('cost', f"${d['cost']:.2f}", 'white', 8, 4))
    return out


def _join(segs, cfg):
    group_sep, part_sep = (' || ', ' | ') if cfg['ascii'] else (' ‖ ', ' · ')
    pieces, previous = [], None
    for seg in segs:
        if previous is not None:
            pieces.append(('', part_sep if previous == seg[4] else group_sep))
        pieces.append((seg[2], seg[1]))
        previous = seg[4]
    return pieces


def _width(segs, cfg):
    return sum(len(text) for _, text in _join(segs, cfg))


def _middle(value, width, ascii):
    if len(value) <= width:
        return value
    keep = max(0, width - 1)
    return value[:keep // 2] + ('~' if ascii else '…') + value[len(value) - (keep - keep // 2):]


def format_status(d, columns=None, cfg=None, now=None, tz=None):
    """The line (ANSI colors when configured), fitted to `columns` (None: no limit)."""
    cfg = cfg or config({})
    now = now or datetime.now(timezone.utc)
    tz = tz or datetime.now().astimezone().tzinfo
    segs = segments(d, cfg, now, tz)
    if columns is not None:
        while _width(segs, cfg) > columns and any(s[3] > 0 for s in segs):
            least = max((s for s in segs if s[3] > 0), key=lambda s: s[3])
            segs.remove(least)
        if _width(segs, cfg) > columns:
            segs = [(s[0], _context(d, True)[0], *s[2:]) if s[0] == 'context' else s for s in segs]
            total = _width(segs, cfg)
            segs = [(s[0], _middle(s[1], max(0, columns - (total - len(s[1]))), cfg['ascii']), *s[2:]) if s[0] == 'project' else s for s in segs]
            if _width(segs, cfg) > columns and any(s[0] == 'context' for s in segs):
                segs = [s for s in segs if s[0] != 'project']
    pieces = _join(segs, cfg)
    plain = ''.join(text for _, text in pieces)
    if columns is not None and len(plain) > columns:
        # last resort, as Codex: cut the line (… unless ASCII)
        cut = columns if cfg['ascii'] else max(0, columns - 1)
        out, used = [], 0
        for color, text in pieces:
            take = text[:max(0, cut - used)]
            used += len(take)
            if take:
                out.append((color, take))
        pieces = out + ([] if cfg['ascii'] else [('', '…')])
    if not cfg['colors']:
        return ''.join(text for _, text in pieces)
    return ''.join(f'\x1b[{COLORS[color]}m{text}\x1b[0m' if color else text for color, text in pieces)


# --- Claude Code -----------------------------------------------------------------

def _windows(raw):
    """Rate-limit windows as a list or a {kind: {...}} map; unknown shapes are no reading."""
    items = raw.items() if isinstance(raw, dict) else ((None, w) for w in raw) if isinstance(raw, list) else ()
    out = []
    for kind, w in items:
        if not isinstance(w, dict):
            continue
        kind = kind or w.get('kind')
        used = next((number(w.get(k)) for k in ('used_percentage', 'percent_used', 'percentUsed', 'utilization') if number(w.get(k)) is not None), None)
        if isinstance(kind, str) and used is not None:
            out.append({'kind': kind, 'used': used, 'resets_at': w.get('resets_at', w.get('resetsAt'))})
    return out


def claude_data(payload, usage=None, git=None, home=None):
    """Normalize Claude Code's status line payload (and the collector's totals)."""
    model = payload.get('model') if isinstance(payload.get('model'), dict) else {}
    workspace = payload.get('workspace') if isinstance(payload.get('workspace'), dict) else {}
    cw = payload.get('context_window') if isinstance(payload.get('context_window'), dict) else {}
    cost = payload.get('cost') if isinstance(payload.get('cost'), dict) else {}
    effort = payload.get('effort')
    effort = effort.get('level') if isinstance(effort, dict) else effort
    data = {
        'cwd': workspace.get('current_dir') or payload.get('cwd') or '',
        'home': home if home is not None else os.environ.get('HOME', ''),
        'model': model.get('id') or model.get('display_name') or '',
        'effort': effort if isinstance(effort, str) else '',
        'tier': '',  # Claude Code reports no service tier
        'context': {'tokens': cw.get('total_input_tokens'), 'window': cw.get('context_window_size'), 'percent': cw.get('used_percentage')},
        'windows': _windows(payload.get('rate_limits')),
        'tokens': None,
        'cost': number(cost.get('total_cost_usd')),
    }
    if git:
        data.update(git)
    if usage:
        data.update(usage)
    return data


def read_usage(root, provider, session):
    """The collector's session totals, the `usage` field of the session record (raw or
    envelope): absent or malformed is no reading."""
    if not root or not isinstance(session, str) or not SAFE.fullmatch(session):
        return None
    try:
        raw = json.loads((Path(root)/'sessions'/provider/(session + '.json')).read_text())
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict) and raw.get('schema_version') == 1 and isinstance(raw.get('data'), dict):
        raw = raw['data']
    raw = raw.get('usage') if isinstance(raw, dict) else None
    if not isinstance(raw, dict):
        return None
    fields = {k: number(raw.get(k)) for k in ('input', 'output', 'cacheRead', 'cacheWrite', 'startedAt', 'since')}
    if any(fields[k] is None for k in ('input', 'output', 'cacheRead', 'cacheWrite')):
        return None
    tokens = {'input': fields['input'], 'output': fields['output'], 'cache_read': fields['cacheRead'], 'cache_write': fields['cacheWrite']}
    since = None
    if fields['startedAt'] is not None and fields['since'] is not None and fields['since'] - fields['startedAt'] > 60_000:
        since = datetime.fromtimestamp(fields['since'] / 1000, timezone.utc)
    return {'tokens': tokens, 'counted_since': since}


def read_git(cwd):
    """Branch and dirtiness from one bounded `git status`; {} when unknown."""
    if not cwd:
        return {}
    try:
        out = subprocess.run(['git', '-C', cwd, '--no-optional-locks', 'status', '--porcelain=v1', '--branch', '--untracked-files=no'],
                             capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}
    lines = [line for line in out.stdout.splitlines() if line]
    match = re.match(r'^## (?:No commits yet on )?([^.\s]+)', lines[0]) if lines else None
    branch = match.group(1) if match else ''
    return {'branch': 'detached' if branch == 'HEAD' else branch, 'dirty': len(lines) > 1}


def state_root(environ=None):
    """In-process resolution (no CLI call per render): explicit env, else default."""
    env = os.environ if environ is None else environ
    explicit = env.get('HARNESS_UI_STATE_DIR') or env.get('CODEX_UI_STATE_DIR')
    if explicit:
        return str(Path(explicit).expanduser())
    return str(Path(env.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'harness-ui-mods')


def render(provider, stdin_text, state_root_path=None, columns=None, git=True, cfg=None, now=None, tz=None, environ=None):
    """One status line for `provider` from its native payload."""
    if provider != 'claude':
        raise ValueError('unsupported provider: ' + str(provider))
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    root = state_root_path if state_root_path is not None else state_root(environ)
    usage = read_usage(root, 'claude', payload.get('session_id'))
    workspace = payload.get('workspace') if isinstance(payload.get('workspace'), dict) else {}
    info = read_git(workspace.get('current_dir') or payload.get('cwd')) if git else {}
    return format_status(claude_data(payload, usage, info), columns, cfg or config(environ), now, tz)
