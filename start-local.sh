#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$repo_dir/app"
bind_address="0.0.0.0:8000"
bind_address_set=false
rebuild_assets=false

usage() {
    cat <<'EOF'
Usage: ./start-local.sh [--rebuild-assets] [host:port]

Start the local hackathon preview. PostgreSQL and Redis must already be running.
Prepare the development database explicitly as documented in README.rst.
Startup checks pending migrations; it does not initialize or publish a workspace.
Use --rebuild-assets after changing frontend sources or static asset inputs.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --rebuild-assets) rebuild_assets=true ;;
        -h|--help) usage; exit 0 ;;
        -*) printf 'Unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
        *)
            if [[ "$bind_address_set" == true ]]; then
                printf 'Only one host:port argument is supported.\n' >&2
                exit 2
            fi
            bind_address="$arg"
            bind_address_set=true
            ;;
    esac
done

if ! command -v uv >/dev/null 2>&1; then
    printf 'uv is required. See the local development section in README.rst.\n' >&2
    exit 1
fi

cd "$app_dir"

# Use the repository's local PostgreSQL instance when one is running, while
# preserving any explicit environment or app configuration.
if [[ -z ${EVY_POSTGRES_HOST:-} && -z ${EVY_POSTGRES_PORT:-} \
    && ! -f .env && ! -f eventyay.local.toml \
    && -f "$repo_dir/.local-services/postgres-data/postmaster.pid" ]]; then
    postgres_pid_file="$repo_dir/.local-services/postgres-data/postmaster.pid"
    postgres_pid="$(sed -n '1p' "$postgres_pid_file")"
    if kill -0 "$postgres_pid" 2>/dev/null; then
        export EVY_POSTGRES_HOST="$(sed -n '6p' "$postgres_pid_file")"
        export EVY_POSTGRES_PORT="$(sed -n '4p' "$postgres_pid_file")"
    fi
fi

uv sync --all-extras --all-groups
. .venv/bin/activate
python manage.py migrate --check

if [[ ! -d eventyay/data/compiled-frontend/schedule \
    || ! -d eventyay/data/compiled-frontend/schedule-editor \
    || ! -d eventyay/data/compiled-frontend/video ]]; then
    rebuild_assets=true
fi

if [[ "$rebuild_assets" == true ]]; then
    make npminstall
fi

python manage.py collectstatic --noinput

if [[ "$rebuild_assets" == true || ! -d eventyay/static.dist/CACHE ]]; then
    python manage.py compress --force
fi

printf 'Starting http://localhost:%s/hackathon/camp/ (Ctrl+C to stop)\n' "${bind_address##*:}"
exec python manage.py run_hackathon_preview "$bind_address" --insecure
