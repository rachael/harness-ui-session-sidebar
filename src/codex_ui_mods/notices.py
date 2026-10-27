"""Quiet, persisted discovery notices; discovery never enables new features."""
import time
import json
import shutil
from .core import Error, read_json


def feature_ids(manager):
    features = {'codex:'+name for name, m in manager.modules().items() if m.get('harness', 'codex') == 'codex' and m['patches']}
    if shutil.which('codex'): features.add('harness:codex')
    if shutil.which('claude'): features.add('harness:claude')
    adapter = read_json(manager.root/'catalog/adapters/claude-code/manifest.json', {})
    if isinstance(adapter, dict):
        if not isinstance(adapter.get('features', []), list):
            raise Error('invalid adapter feature metadata')
        for feature in adapter.get('features', []):
            if isinstance(feature, dict) and isinstance(feature.get('id'), str):
                features.add('claude:'+feature['id'])
    return features


def baseline(manager):
    manager.config['feature_notices'] = {'known': sorted(feature_ids(manager)), 'pending': [], 'shown': [], 'suppressed': False}


def notice(manager, action='poll', hours=24, now=None):
    now = time.time() if now is None else now
    before = json.dumps(manager.config.get('feature_notices'), sort_keys=True)
    state = manager.config.setdefault('feature_notices', {'known': [], 'pending': [], 'shown': [], 'suppressed': False})
    if not isinstance(state, dict) or any(not isinstance(state.get(key), list) or not all(isinstance(item, str) for item in state[key]) for key in ('known', 'pending', 'shown')) or type(state.get('suppressed')) is not bool:
        raise Error('invalid optional-feature notice state')
    if state.get('remind_at') is not None and not isinstance(state['remind_at'], (int, float)):
        raise Error('invalid optional-feature reminder time')
    available = feature_ids(manager)
    new = available-set(state['known'])
    state['known'] = sorted(set(state['known']) | available)
    state['pending'] = sorted((set(state['pending']) | new) & available)
    if action == 'suppress':
        state['suppressed'] = True
    elif action == 'dismiss':
        state['pending'] = []; state['shown'] = []; state.pop('remind_at', None)
    elif action == 'remind-later':
        state['remind_at'] = now+hours*3600
    elif action == 'resume':
        state['suppressed'] = False; state['shown'] = []
    ready = []
    if action == 'status':
        ready = state['pending']
    elif action == 'poll' and not state['suppressed']:
        reminder = state.get('remind_at')
        if reminder is None or now >= reminder:
            ready = state['pending'] if reminder is not None else sorted(set(state['pending'])-set(state['shown']))
            state['shown'] = sorted(set(state['shown']) | set(ready))
            state.pop('remind_at', None)
    if json.dumps(state, sort_keys=True) != before:
        manager.save()
    return {'features': ready, 'pending': state['pending'], 'suppressed': state['suppressed'], 'remind_at': state.get('remind_at')}
