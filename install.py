#!/usr/bin/env python3
"""Generated bootstrap; implementation is maintained in harness-ui-mods."""
import shutil
import subprocess
import sys

SPEC = "git+https://github.com/rachael/harness-ui-mods.git"
if shutil.which("uv"):
    subprocess.run(["uv", "tool", "install", "--force", SPEC], check=True)
    command = ["uv", "tool", "run", "--from", SPEC, "harness-ui", "install"]
elif shutil.which("pipx"):
    subprocess.run(["pipx", "install", "--force", SPEC], check=True)
    command = ["pipx", "run", "--spec", SPEC, "harness-ui", "install"]
else:
    raise SystemExit("Install uv or pipx first, then rerun this bootstrap.")
print("Component presets add to existing choices. Existing off choices are preserved; --replace-selection replaces them deliberately.")
raise SystemExit(subprocess.call(command+['--module', 'session-sidebar']+sys.argv[1:]))
