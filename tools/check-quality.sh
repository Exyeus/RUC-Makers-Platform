#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
base="${1:-HEAD}"
out="${QUALITY_OUTPUT:-.local-services/quality-evidence}"
mkdir -p "$out"
python3 .agents/quality/scripts/verify_upstream.py
python3 -m unittest discover -s .agents/quality/tests -v > "$out/toolkit-tests.log" 2>&1
if [[ "$base" == full ]]; then
    python3 .agents/quality/scripts/check_repo.py --out "$out/full-scan.json"
else
    python3 .agents/quality/scripts/check_repo.py --diff-base "$base" --out "$out/changed-lines.json"
fi
