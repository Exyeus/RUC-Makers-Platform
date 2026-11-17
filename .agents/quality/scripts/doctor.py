#!/usr/bin/env python3
"""Read-only local prerequisites report. Does not install, connect to servers, or inspect secrets."""
import argparse
import datetime
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    checks = []
    for name, arguments in [('git', ['--version']), ('node', ['--version']), ('npm', ['--version']), ('uv', ['--version']), ('playwright-cli', ['--version']), ('codex', ['--version'])]:
        path = shutil.which(name)
        entry = {'tool': name, 'available': bool(path), 'path': path}
        if path:
            try:
                p = subprocess.run([path, *arguments], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
                entry.update(exit_code=p.returncode, version=(p.stdout or p.stderr).strip()[:500])
            except (OSError, subprocess.TimeoutExpired) as exc:
                entry.update(error=str(exc))
        checks.append(entry)
    report = {'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'os': platform.platform(), 'is_wsl': 'microsoft' in platform.release().lower(),
              'python': sys.version.split()[0], 'python_executable': sys.executable,
              'cwd': str(Path.cwd()), 'tools': checks,
              'not_checked': ['Browser launch and fonts', 'Codex subagent/hook capabilities and approvals',
                              'Network connectivity / QA origin / TLS', 'Database and object-storage isolation',
                              'Native Windows display scaling', 'Dependencies installed only locally in node_modules']}
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
    else:
        print(text, end='')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
