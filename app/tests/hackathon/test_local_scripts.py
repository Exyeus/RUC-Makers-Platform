import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_preview_startup_checks_migrations_without_initializing_or_publishing(tmp_path):
    bash = shutil.which('bash')
    if bash is None:
        pytest.skip('Local preview scripts require Bash.')
    source = Path(__file__).resolve().parents[3] / 'start-local.sh'
    script = tmp_path / 'start-local.sh'
    shutil.copyfile(source, script)
    app = tmp_path / 'app'
    (app / '.venv/bin').mkdir(parents=True)
    (app / '.venv/bin/activate').write_text('')
    for name in ['schedule', 'schedule-editor', 'video']:
        (app / 'eventyay/data/compiled-frontend' / name).mkdir(parents=True)
    (app / 'eventyay/static.dist/CACHE').mkdir(parents=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for command in ['uv', 'python', 'make']:
        stub = binaries / command
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "{command} $*" >> "$EVY_SCRIPT_TEST_LOG"\n')
        stub.chmod(0o755)
    log = tmp_path / 'commands.log'
    environment = {
        'PATH': f'{binaries}{os.pathsep}{os.environ["PATH"]}',
        'EVY_SCRIPT_TEST_LOG': str(log),
    }
    result = subprocess.run(
        [bash, str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    assert 'python manage.py migrate --check' in commands
    assert 'python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure' in commands
    assert not any(command.startswith('python manage.py setup_hackathon') for command in commands)
    assert not any(
        command.startswith('python manage.py migrate') and command != 'python manage.py migrate --check'
        for command in commands
    )
