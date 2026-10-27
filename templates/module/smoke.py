"""Minimal startup/option smoke for the data-only starter; never submits a turn."""
import os
import subprocess
import sys


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 smoke.py /path/to/codex')
    flag = os.environ.get('HARNESS_UI_EXAMPLE_FLAG', '1')
    if flag not in ('0', '1'):
        raise SystemExit('Example flag must be 0 or 1')
    result = subprocess.run([sys.argv[1], '--version'], capture_output=True, text=True, timeout=20)
    if result.returncode or 'codex' not in result.stdout.lower():
        raise SystemExit('Candidate did not identify itself as Codex')
    print('Candidate starts; example option='+flag+' (no inference)')


if __name__ == '__main__': main()
