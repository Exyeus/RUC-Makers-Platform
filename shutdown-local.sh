#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$repo_dir/app"
bind_address="0.0.0.0:8000"
bind_address_set=false
dry_run=false

usage() {
    cat <<'EOF'
Usage: ./shutdown-local.sh [--dry-run] [host:port]

Stop this checkout's local hackathon preview at the given address.
The default is 0.0.0.0:8000, matching start-local.sh.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) dry_run=true ;;
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

if [[ ! -d /proc ]] || ! command -v pgrep >/dev/null 2>&1; then
    printf 'This shutdown script requires Linux /proc and pgrep.\n' >&2
    exit 1
fi

pids=()
mapfile -t candidates < <(pgrep -f 'manage.py run_hackathon_preview' || true)
for pid in "${candidates[@]}"; do
    process_dir="/proc/$pid"
    process_cwd="$(readlink "$process_dir/cwd" 2>/dev/null)" || continue
    [[ "$process_cwd" == "$app_dir" ]] || continue
    [[ -r "$process_dir/cmdline" ]] || continue
    mapfile -d '' -t command < "$process_dir/cmdline" 2>/dev/null || continue
    (( ${#command[@]} >= 4 )) || continue
    [[ "${command[0]##*/}" == python* \
        && "${command[1]}" == manage.py \
        && "${command[2]}" == run_hackathon_preview ]] || continue
    for arg in "${command[@]:3}"; do
        if [[ "$arg" == "$bind_address" ]]; then
            pids+=("$pid")
            break
        fi
    done
done

if (( ${#pids[@]} == 0 )); then
    printf 'No local preview found at %s.\n' "$bind_address"
    exit 0
fi

if [[ "$dry_run" == true ]]; then
    printf 'Would stop local preview at %s (PID: %s).\n' "$bind_address" "${pids[*]}"
    exit 0
fi

for pid in "${pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
done

for _ in {1..50}; do
    still_running=false
    for pid in "${pids[@]}"; do
        if [[ -s "/proc/$pid/cmdline" ]]; then
            still_running=true
            break
        fi
    done
    if [[ "$still_running" == false ]]; then
        printf 'Stopped local preview at %s.\n' "$bind_address"
        exit 0
    fi
    sleep 0.2
done

printf 'Preview at %s did not stop within 10 seconds (PID: %s).\n' "$bind_address" "${pids[*]}" >&2
exit 1
