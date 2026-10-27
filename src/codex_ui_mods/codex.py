"""Native Codex events populate the same overview contract as other harnesses."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time

if __name__ == '__main__':
    from .bootstrap import dispatch
    dispatch('codex_ui_mods.codex')

from .core import Error, Manager, atomic_json
from .sessions import announce
from .surface import (publisher_command, read_record, resolve_state, session_lock,
                      session_path, write_record)

TOOL_MATCHER = r'(^|[.:])(?:update_plan|request_user_input(?:_async)?)$'


def update_record(previous, event):
    """Pure event reduction; explicit answers close only their matching tool asks."""
    record = deepcopy(previous)
    record.update(harness='Codex', session=event['session_id'])
    for field in ('cwd', 'model'):
        if isinstance(event.get(field), str):
            record[field] = event[field]
    views = record.setdefault('views', {})
    if not isinstance(views, dict):
        raise Error('session views must be an object')
    view = views.setdefault('main', {'answer': '', 'answerAt': 0, 'asks': [], 'tasks': []})
    if not isinstance(view, dict) or not isinstance(view.get('asks', []), list):
        raise Error('invalid main view')
    kind = event.get('hook_event_name')
    if kind == 'UserPromptSubmit' and isinstance(event.get('prompt'), str):
        if not record.get('title'):
            record['title'] = next((s.strip() for s in event['prompt'].splitlines() if s.strip()), '')
    elif kind == 'Stop' and isinstance(event.get('last_assistant_message'), str):
        answer = event['last_assistant_message']
        if answer != view.get('answer'):
            view.update(answer=answer, answerAt=int(time.time()*1000))
    elif kind in ('PreToolUse', 'PostToolUse'):
        tool = re.split(r'[.:]', event.get('tool_name', ''))[-1]
        args = event.get('tool_input') or {}
        if not isinstance(args, dict):
            return record
        if tool == 'update_plan' and kind == 'PostToolUse':
            plan = args.get('plan', [])
            if isinstance(plan, list):
                view['tasks'] = [{'id': f'plan:{i}', 'subject': step['step'],
                                  'status': step.get('status', 'pending')}
                                 for i, step in enumerate(plan)
                                 if isinstance(step, dict) and isinstance(step.get('step'), str)]
        elif tool in ('request_user_input', 'request_user_input_async'):
            call = event.get('tool_use_id')
            if not isinstance(call, str) or not call:
                call = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:20]
            prefix = 'tool:' + call + ':'
            asks = view.setdefault('asks', [])
            questions = args.get('questions', [])
            if kind == 'PreToolUse' and isinstance(questions, list):
                for i, question in enumerate(questions):
                    if not isinstance(question, dict):
                        continue
                    text = question.get('question', question.get('title'))
                    identity = prefix + str(question.get('id', i))
                    if isinstance(text, str) and text.strip() and not any(a.get('id') == identity for a in asks if isinstance(a, dict)):
                        asks.append({'id': identity, 'text': text, 'from': 'tool',
                                     'source_anchor': call})
            response = event.get('tool_response')
            # Async acceptance is not an answer. Keep the ask until actual answers arrive.
            if kind == 'PostToolUse' and isinstance(response, dict) and response.get('answers'):
                answers = response['answers']
                if isinstance(answers, dict):
                    answered = {prefix+str(key) for key, value in answers.items()
                                if isinstance(value, dict) and value.get('answers')}
                    view['asks'] = [a for a in asks if not isinstance(a, dict) or a.get('id') not in answered]
    # Process liveness is separate from work/attention. These states come only
    # from native events; an asynchronous ask can coexist with ongoing work.
    status = None
    if kind == 'SessionStart':
        status = 'unknown'
    elif kind in ('Stop', 'Interrupt'):
        status = 'idle'
    elif kind in ('UserPromptSubmit', 'PreToolUse', 'PostToolUse'):
        status = 'working'
        if kind == 'PreToolUse' and re.split(r'[.:]', event.get('tool_name', ''))[-1] == 'request_user_input' and view.get('asks'):
            status = 'awaiting_input'
    if status is not None:
        activity = {'status': status, 'needs_input': bool(view.get('asks')),
                    'source': 'native-event'}
        previous_activity = record.get('activity')
        if not isinstance(previous_activity, dict) or any(previous_activity.get(k) != v for k, v in activity.items()):
            record['activity'] = dict(activity, updatedAt=int(time.time()*1000))
    return record


def hook(event, manager=None):
    if not isinstance(event, dict) or not isinstance(event.get('session_id'), str):
        raise Error('hook requires a session identifier')
    manager = manager or Manager()
    enabled = {module['id'] for module in manager.selection(None)}
    if not enabled.intersection(('info-pane', 'session-sidebar')):
        return {'changed': False, 'reason': 'overview and session sidebar are disabled'}
    settings = resolve_state('codex', manager)
    environment = dict(os.environ, **settings['environment'])
    path = session_path(Path(settings['state_root']), 'codex', event['session_id'])
    # Presence shares the publisher and record, and takes its own lock. Announce
    # before the overview lock so a failed attempt can retry on SessionStart.
    if event.get('hook_event_name') == 'SessionStart' and 'session-sidebar' in enabled:
        announce('codex', event['session_id'], root=Path(settings['state_root']),
                 environ=environment, manager=manager)
    with session_lock(path):
        previous, revision = read_record(path, event['session_id'])
        updated = update_record(previous, event)
        if updated == previous:
            return {'changed': False}
        updated['updated'] = int(time.time()*1000)
        write_record(path, updated, revision,
                     publisher_command('codex', event['session_id'], environment), environment)
        stored, _ = read_record(path, event['session_id'])
        if stored != updated:
            raise Error('overview publication did not pass read-back')
        return {'changed': True}


def install_hooks(path=None, apply=True, python=None):
    """Merge only our exact module command, keeping other groups and hook trust intact."""
    path = Path(path or Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'hooks.json')
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original) if original else {}
    if not isinstance(config, dict) or not isinstance(config.get('hooks', {}), dict):
        raise Error('invalid Codex hooks configuration')
    command = shlex.join([python or sys.executable, '-P', '-m', 'codex_ui_mods.codex', 'hook'])
    hooks = config.setdefault('hooks', {})
    changed = False
    for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'Interrupt', 'PreToolUse', 'PostToolUse'):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise Error('invalid hook groups')
        wanted = {'hooks': [{'type': 'command', 'command': command, 'timeout': 20}]}
        if event.endswith('ToolUse'):
            wanted['matcher'] = TOOL_MATCHER
        if wanted in groups:
            continue
        def owned(item):
            try:
                words = shlex.split(item.get('command', ''))
                return words[1:] in (['-m', 'codex_ui_mods.codex', 'hook'],
                                     ['-P', '-m', 'codex_ui_mods.codex', 'hook'])
            except (ValueError, AttributeError):
                return False
        replaced = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise Error('invalid hook group')
            remaining = [item for item in group.get('hooks', []) if not owned(item)]
            if remaining:
                replaced.append(dict(group, hooks=remaining))
        hooks[event] = replaced+[wanted]
        changed = True
    if apply and changed:
        if original is not None:
            backup = path.with_name(path.name+'.before-harness-ui-'+hashlib.sha256(original).hexdigest()[:12])
            if backup.exists() and backup.read_bytes() != original:
                raise Error('hook backup conflicts; no configuration changed')
            if not backup.exists():
                backup.write_bytes(original)
        atomic_json(path, config)
    return {'changed': changed, 'applied': apply and changed, 'path': str(path),
            'note': 'Review newly installed hooks with /hooks in Codex.'}


def main():
    try:
        if sys.argv[1:] == ['hook']:
            hook(json.load(sys.stdin))
        elif sys.argv[1:] in (['install-hooks'], ['install-hooks', '--check']):
            print(json.dumps(install_hooks(apply='--check' not in sys.argv)))
        else:
            raise Error('use hook or install-hooks [--check]')
    except (Error, OSError, ValueError, TypeError) as error:
        print('harness-ui overview: '+str(error), file=sys.stderr)
        # A display failure must not interrupt the user's tool or turn.
        if sys.argv[1:] != ['hook']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
