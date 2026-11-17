"""Run an explicit management command as the service user with private settings."""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

root = Path("/srv/ruc-makers")
configuration = json.loads((root / "private/site.json").read_text())
user = pwd.getpwnam("rucmakers")
subprocess.run(
    [str(root / "src/app/.venv/bin/python"), "manage.py", *sys.argv[1:]],
    cwd=root / "src/app",
    env={
        **os.environ,
        **configuration,
        "HOME": user.pw_dir,
        "USER": user.pw_name,
        "LOGNAME": user.pw_name,
    },
    user=user.pw_uid,
    group=user.pw_gid,
    extra_groups=(),
    umask=0o077,
    check=True,
)
