#!/usr/bin/env bash
set -euo pipefail
export PYTHONOPTIMIZE=0
cd "$(git rev-parse --show-toplevel)"
ROOT=$PWD
: "${CI_POSTGRES_HOST:?Set to a disposable PostgreSQL service}"
[[ "$CI_POSTGRES_HOST" == 127.0.0.1 ]] || { echo 'QA browser requires loopback isolated PostgreSQL'; exit 2; }
export QA_OUTPUT=${QA_OUTPUT:-$(mktemp -d /tmp/ruc-quality-browser-XXXXXX)}
export QA_DATABASE=ruc_quality_$(date +%s)_$RANDOM
export QA_PYTHON=${QA_PYTHON:-$ROOT/.venv-wsl/bin/python}
export PYTHONPATH=$ROOT/app:$ROOT/tools/quality-browser
export DJANGO_SETTINGS_MODULE=qa_settings
export PATH=$ROOT/tools/quality-browser/node_modules/node-linux-x64/bin:$PATH
export PGHOST=$CI_POSTGRES_HOST PGPORT=${CI_POSTGRES_PORT:-5432} PGUSER=${CI_POSTGRES_USER:-eventyay} PGPASSWORD=${CI_POSTGRES_PASSWORD:-}
mkdir -p "$QA_OUTPUT"
server_pid=''
cleanup() { if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi; dropdb --if-exists "$QA_DATABASE"; }
trap cleanup EXIT
"$QA_PYTHON" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",8779));s.close()'
createdb "$QA_DATABASE"
"$QA_PYTHON" app/manage.py migrate --noinput >"$QA_OUTPUT/migrations.log" 2>&1
"$QA_PYTHON" tools/quality-browser/seed.py >"$QA_OUTPUT/seed.log" 2>&1
"$QA_PYTHON" tools/quality-browser/serve.py >"$QA_OUTPUT/server.log" 2>&1 &
server_pid=$!
for attempt in {1..60}; do
  kill -0 "$server_pid" || { cat "$QA_OUTPUT/server.log"; exit 2; }
  if curl --fail --silent http://127.0.0.1:8779/hackathon/quality/ >/dev/null; then break; fi
  sleep 1
done
"$QA_PYTHON" .agents/quality/scripts/source_identity.py >"$QA_OUTPUT/identity.json"
set +e
node tools/quality-browser/node_modules/@playwright/test/cli.js test --config tools/quality-browser/playwright.config.cjs
result=$?
set -e
[[ "$result" == 0 ]] || exit "$result"
"$QA_PYTHON" .agents/quality/scripts/source_identity.py >"$QA_OUTPUT/identity-end.json"
cmp "$QA_OUTPUT/identity.json" "$QA_OUTPUT/identity-end.json" || { echo 'Source changed during browser run'; exit 1; }
read -r revision fingerprint < <("$QA_PYTHON" -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["revision"],d["source_fingerprint"])' "$QA_OUTPUT/identity-end.json")
"$QA_PYTHON" .agents/quality/scripts/gate_evidence.py --report "$QA_OUTPUT/report.json" --revision "$revision" --source-fingerprint "$fingerprint" --scope "${QA_SCOPE:-all}"

