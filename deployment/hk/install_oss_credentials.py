"""Receive temporary STS credentials over stdin, without OAuth refresh tokens."""

import json
import os
import pwd
import sys
import tempfile
import time
from pathlib import Path

values = json.load(sys.stdin)
fields = {"access_key_id", "access_key_secret", "session_token", "expires_at"}
if not isinstance(values, dict) or set(values) != fields:
    raise SystemExit("Only the four temporary credential fields are accepted.")
if not all(
    isinstance(values[key], str) and values[key] for key in fields - {"expires_at"}
):
    raise SystemExit("Incomplete temporary credentials.")
expiry = values["expires_at"]
if (
    not isinstance(expiry, int)
    or isinstance(expiry, bool)
    or expiry <= time.time() + 120
):
    raise SystemExit("Temporary credentials expire too soon.")
root = Path("/srv/ruc-makers/credentials")
owner = pwd.getpwnam("rucmakers")
fd, temporary = tempfile.mkstemp(dir=root, prefix=".oss-")
os.fchown(fd, owner.pw_uid, owner.pw_gid)
with os.fdopen(fd, "w") as output:
    json.dump(values, output)
    output.flush()
    os.fsync(output.fileno())
os.replace(temporary, root / "oss-sts.json")
print("Temporary OSS credential file updated; expiry epoch=" + str(expiry))
