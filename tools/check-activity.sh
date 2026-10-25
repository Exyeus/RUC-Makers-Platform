#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
: "${CI_POSTGRES_HOST:?Set CI_POSTGRES_HOST to an isolated test PostgreSQL service}"
export EVY_RUNNING_ENVIRONMENT=testing EVY_DOCS_BUILD=1

bash tools/validate_skill_layout.sh
bash -n start-local.sh shutdown-local.sh tools/validate_skill_layout.sh tools/check-activity.sh
for script in app/eventyay/static/hackathon/*.js; do
    node --check --input-type=module < "$script"
done

cd app
node tests/hackathon/test_upload_hash.mjs
uv run --no-sync ruff check eventyay/hackathon tests/hackathon
uv run --no-sync python manage.py check --settings=tests.hackathon.ci_settings
uv run --no-sync python manage.py makemigrations --check --dry-run hackathon --settings=tests.hackathon.ci_settings
uv run --no-sync python -m pytest --ds=tests.hackathon.ci_settings tests/hackathon -q -p no:sugar
