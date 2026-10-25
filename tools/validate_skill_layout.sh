#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
python3 - <<'PY'
from pathlib import Path
import re
import sys

root = Path('.agents/skills')
errors = []
if not root.is_dir():
    errors.append('missing .agents/skills')
skills = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
for directory in skills:
    entry = directory / 'SKILL.md'
    if not entry.is_file():
        errors.append(f'{directory}: missing SKILL.md')
        continue
    text = entry.read_text(encoding='utf-8')
    frontmatter = re.match(r'\A---\n(.*?)\n---(?:\n|$)', text, re.S)
    if not frontmatter:
        errors.append(f'{entry}: missing YAML frontmatter')
        continue
    fields = frontmatter.group(1)
    name = re.search(r'^name: *([a-z0-9-]+) *$', fields, re.M)
    if not name or name.group(1) != directory.name:
        errors.append(f'{entry}: name must match the skill directory')
    if not re.search(r'^description: *\S', fields, re.M):
        errors.append(f'{entry}: missing description')
    for child in directory.iterdir():
        if child.is_dir() and not any(p.is_file() for p in child.rglob('*')):
            errors.append(f'{child}: empty optional resource directory')
for error in errors:
    print(f'ERROR: {error}', file=sys.stderr)
if errors:
    sys.exit(1)
print(f'OK: {len(skills)} skills have SKILL.md; supporting resources are optional.')
PY
