"""Execute the deployment regression suite with the service identity."""

import json
import os
import pwd
import subprocess
from pathlib import Path

root = Path("/srv/ruc-makers")
configuration = json.loads((root / "private/site.json").read_text())
owner = pwd.getpwnam("rucmakers")
env = {
    **os.environ,
    **configuration,
    "EVY_RUNNING_ENVIRONMENT": "testing",
    "DJANGO_SETTINGS_MODULE": "rucmakers_test_settings",
    "HOME": owner.pw_dir,
    "USER": owner.pw_name,
    "LOGNAME": owner.pw_name,
    "HACKATHON_TEST_S3_ENDPOINT": configuration["EVY_HACKATHON_S3_ENDPOINT"],
    "HACKATHON_TEST_S3_REGION": configuration["EVY_HACKATHON_S3_REGION"],
    "HACKATHON_TEST_S3_BUCKET": configuration["EVY_HACKATHON_S3_BUCKET"],
    "HACKATHON_TEST_S3_SIGNATURE_VERSION": "s3v4",
    "HACKATHON_TEST_S3_CREDENTIAL_FILE": configuration[
        "EVY_HACKATHON_S3_CREDENTIAL_FILE"
    ],
}
subprocess.run(
    [
        str(root / "src/app/.venv/bin/python"),
        "-m",
        "pytest",
        "-p",
        "rucmakers_test_plugin",
        "--ds=rucmakers_test_settings",
        "--reuse-db",
        "tests/hackathon",
        "-q",
        "--tb=short",
        "--disable-warnings",
        "-o",
        "cache_dir=/srv/ruc-makers/runtime/test-cache",
    ],
    cwd=root / "src/app",
    env=env,
    user=owner.pw_uid,
    group=owner.pw_gid,
    extra_groups=(),
    umask=0o077,
    check=True,
)
