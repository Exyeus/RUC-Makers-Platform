import json
import os
import sys
import tempfile
import time
from pathlib import Path

payload = json.load(sys.stdin)
keys = {"access_key_id", "access_key_secret", "session_token", "expires_at"}
if set(payload) != keys or not all(
    isinstance(payload[k], str) and payload[k] for k in keys - {"expires_at"}
):
    raise SystemExit("Invalid STS credential format")
if (
    not isinstance(payload["expires_at"], int)
    or payload["expires_at"] <= time.time() + 120
):
    raise SystemExit("STS credentials expire too soon")
root = Path.home() / ".config/rucmakers"
fd, temporary = tempfile.mkstemp(dir=root, prefix=".sts-")
with os.fdopen(fd, "w") as output:
    json.dump(payload, output)
    output.flush()
    os.fsync(output.fileno())
os.replace(temporary, root / "oss-sts.json")
print("Temporary OSS credentials installed; expires_at=" + str(payload["expires_at"]))
