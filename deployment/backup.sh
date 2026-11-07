#!/usr/bin/env bash
# Run from deployment/. Stops writers; intentionally leaves them stopped on any failure.
set -euo pipefail
umask 077
output=${1:?Usage: bash backup.sh /absolute/new-backup-directory}
[[ "$output" = /* && ! -e "$output" ]] || { echo 'Require a new absolute backup directory' >&2; exit 2; }
mkdir -p "$output"
docker compose stop proxy web websocket worker beat
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$output/database.dump"
docker compose run --rm --no-deps -T --user root web tar -C /data -czf - . > "$output/data.tar.gz"
docker compose config --images > "$output/images.txt"
(cd "$output" && sha256sum database.dump data.tar.gz > SHA256SUMS)
echo 'Writers remain STOPPED. Snapshot/version the S3 bucket now (if enabled), then restart explicitly.'
