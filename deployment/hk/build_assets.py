"""Build translation catalogs in the operator-owned, read-only runtime source tree."""

import json
import os
import subprocess
from pathlib import Path

root = Path("/srv/ruc-makers")
configuration = json.loads((root / "private/site.json").read_text())
for command in (["compilemessages", "-i", ".venv"], ["compilejsi18n"]):
    subprocess.run(
        [str(root / "src/app/.venv/bin/python"), "manage.py", *command],
        cwd=root / "src/app",
        env={**os.environ, **configuration},
        check=True,
    )
