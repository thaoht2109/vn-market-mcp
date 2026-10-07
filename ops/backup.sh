#!/usr/bin/env bash
# pg_dump of the vn-market-mcp database (jobs, runs, prices, watchlists, positions, alert prefs),
# run inside the postgres container so the dump always matches the server version (the host's
# pg_dump may be older and refuse). Custom format, mode 600, newest $DB_BACKUP_KEEP kept.
# Roles are not in the dump: on a new server restore with --no-owner --no-acl, then run
# db.setup_roles (README §13). restore_test.sh checks the newest dump.
#
# Usage: ops/backup.sh     (env: BACKUP_DIR, DB_BACKUP_KEEP)
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${BACKUP_DIR:-$HOME/backups/db}"
KEEP="${DB_BACKUP_KEEP:-14}"
FILE="$OUT/vnmcp_$(date +%Y%m%d_%H%M%S).dump"

mkdir -p "$OUT" && chmod 700 "$OUT"
umask 077
# Written to .part first: a failed dump never looks like a good backup or rotates one out.
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c' > "$FILE.part"
mv "$FILE.part" "$FILE"
ls -1t "$OUT"/vnmcp_*.dump | tail -n +$((KEEP + 1)) | xargs -r rm --
echo "backup written to $FILE ($(du -h "$FILE" | cut -f1))"
