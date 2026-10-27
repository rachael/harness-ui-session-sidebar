#!/usr/bin/env python3
"""The plugin's own copy of the harness-ui state commands, so a marketplace install needs
nothing else installed (Python 3.11+ is the one requirement). Local files only: no
network, no harness launch, no build.

  [--home H] state paths --provider P
  state publish-session P SESSION --input-file F [--retain-field X ...] [--merge] [--consume-input]
  statusline render --provider claude [--compose-backup F] [--columns N]
  sessions list|focus|announce|forget|layout|sidebar ...   (the session sidebar)

The same argv as `harness-ui`, which the plugin uses instead when HARNESS_UI_COMMAND names
it. The package beside this file is a generated copy of the shared sources.
"""
import argparse
import json
import os
from pathlib import Path
import sys

# Nothing is written beside the plugin's files (an installed copy lives in Claude Code's cache).
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_ui_mods.core import Error  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(prog='harness-ui-state')
    parser.add_argument('--home', help='manager home whose configuration (data modules) applies')
    sub = parser.add_subparsers(dest='action', required=True)
    state = sub.add_parser('state').add_subparsers(dest='state_action', required=True)
    paths = state.add_parser('paths')
    paths.add_argument('--provider', default='codex')
    publish = state.add_parser('publish-session')
    publish.add_argument('provider')
    publish.add_argument('session')
    publish.add_argument('--input-file', type=Path, required=True)
    publish.add_argument('--retain-field', action='append', default=[])
    publish.add_argument('--merge', action='store_true')
    publish.add_argument('--consume-input', action='store_true')
    status = sub.add_parser('statusline').add_subparsers(dest='status_action', required=True)
    render = status.add_parser('render')
    render.add_argument('--provider', required=True)
    render.add_argument('--compose-backup', type=Path)
    render.add_argument('--columns', type=int)
    from codex_ui_mods import sessions
    sessions.add_cli(sub)
    args = parser.parse_args(argv)
    if args.action == 'sessions':
        try:
            # The manager of --home (data modules, publisher) applies to the session commands
            # as to every other.
            from codex_ui_mods.core import Manager
            return sessions.handle(args, manager=Manager(args.home) if args.home else None)
        except Error as e:
            print(f'harness-ui-state: {e}', file=sys.stderr)
            return 1
    try:
        from codex_ui_mods.core import Manager
        from codex_ui_mods.surface import resolve_state
        manager = Manager(args.home) if args.home else None
        if args.action == 'state' and args.state_action == 'paths':
            print(json.dumps(resolve_state(args.provider, manager)))
        elif args.action == 'state':
            from codex_ui_mods.surface import publish_session
            print(json.dumps(publish_session(args.provider, args.session, args.input_file, args.retain_field,
                                             args.consume_input, manager=manager, merge=args.merge)))
        else:
            if args.provider != 'claude':
                raise Error('statusline render supports --provider claude')
            from codex_ui_mods import claude_statusline
            # The same resolution as the writers (explicit environment, then enabled data
            # modules of this or the default home), so usage is read where it was written.
            settings = resolve_state('claude', manager)
            environ = dict(os.environ, **settings['environment'])
            print(claude_statusline.render(sys.stdin.read(), args.compose_backup, args.columns,
                                           state_root=settings['state_root'], environ=environ))
    except Error as e:
        print(f'harness-ui-state: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
