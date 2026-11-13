"""Launch the 167 preview with an external, private OSS runtime configuration."""

import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = Path(
    os.environ.get(
        "RUCMAKERS_OSS_RUNTIME", Path.home() / ".config/rucmakers/oss-runtime.json"
    )
)
values = json.loads(config.read_text())
if not isinstance(values, dict) or not all(
    isinstance(key, str) and key.startswith("EVY_") and isinstance(value, str)
    for key, value in values.items()
):
    raise SystemExit("Invalid OSS runtime configuration.")
os.environ.update(values)
os.chdir(root / "app")
python = root / "app/.venv/bin/python"
os.execv(
    python,
    [
        str(python),
        "manage.py",
        "run_hackathon_preview",
        *(sys.argv[1:] or ["0.0.0.0:8000", "--insecure", "--noreload"]),
    ],
)
