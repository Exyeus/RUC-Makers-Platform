#!/usr/bin/env bash
# Restores local disk + DB to an empty isolated Compose project; never starts application services.
set -euo pipefail
backup=${1:?Usage: bash restore.sh /absolute/backup-directory}
[[ "$backup" = /* ]] || exit 2
(cd "$backup" && sha256sum -c SHA256SUMS)
docker compose stop proxy web websocket worker beat
# Require an empty DB. The operator must create a new project/volumes for restoration.
docker compose run --rm --no-deps -T --user root web sh -ec 'test -z "$(ls -A /data)"'
count=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()"')
[[ "$count" = 0 ]] || { echo 'Refusing restoration into a nonempty database' >&2; exit 2; }
docker compose exec -T db sh -c 'pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$backup/database.dump"
docker compose run --rm --no-deps -T --user root web sh -ec 'test -z "$(ls -A /data)"; tar -C /data -xzf -; chown -R 1000:1000 /data' < "$backup/data.tar.gz"
echo 'Restored DB and local data. Restore/verify S3 separately before starting services.'
