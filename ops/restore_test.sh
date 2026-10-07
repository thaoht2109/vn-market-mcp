#!/usr/bin/env bash
# Restores the newest ops/backup.sh dump into a scratch database in the same postgres container,
# compares row counts of key tables with the live DB, then drops the scratch DB.
#
# Usage: ops/restore_test.sh     (env: BACKUP_DIR)
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${BACKUP_DIR:-$HOME/backups/db}"
LATEST=$(ls -t "$OUT"/vnmcp_*.dump | head -1)
TEST_DB="vnmcp_restore_test"
pg() { docker compose exec -T postgres sh -c "$1"; }

pg "psql -U \"\$POSTGRES_USER\" -d postgres -qc 'DROP DATABASE IF EXISTS $TEST_DB' -c 'CREATE DATABASE $TEST_DB'"
pg "pg_restore -U \"\$POSTGRES_USER\" -d $TEST_DB --no-owner --no-acl --exit-on-error" < "$LATEST"

echo "restore test of $LATEST: comparing row counts"
STATUS=0
for TABLE in runs predictions prices_daily jobs positions watchlist_extra alert_prefs; do
  ORIG=$(pg "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc 'SELECT count(*) FROM $TABLE'")
  RESTORED=$(pg "psql -U \"\$POSTGRES_USER\" -d $TEST_DB -tAc 'SELECT count(*) FROM $TABLE'")
  # The live DB keeps changing after the dump (new jobs, retention deletes), so counts may differ a
  # little; a table that has rows live but none restored is a broken dump.
  if [ "$ORIG" -gt 0 ] && [ "$RESTORED" -eq 0 ]; then echo "EMPTY in restore: $TABLE (live $ORIG)"; STATUS=1
  else echo "$TABLE: OK (live $ORIG, restored $RESTORED)"; fi
done

pg "psql -U \"\$POSTGRES_USER\" -d postgres -qc 'DROP DATABASE $TEST_DB'"
[ "$STATUS" = 0 ] && echo "restore test passed"
exit $STATUS
