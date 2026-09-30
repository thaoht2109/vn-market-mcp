#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-./backups}"
LATEST=$(ls -t "$BACKUP_DIR"/*.dump | head -1)
TEST_DB="vnmcp_restore_test"

psql "$DATABASE_URL" -c "DROP DATABASE IF EXISTS $TEST_DB"
psql "$DATABASE_URL" -c "CREATE DATABASE $TEST_DB"
RESTORE_URL=$(echo "$DATABASE_URL" | sed "s#/[^/]*\$#/$TEST_DB#")
pg_restore -d "$RESTORE_URL" "$LATEST"

echo "restore test: comparing row counts for runs, predictions, prices_daily"
for TABLE in runs predictions prices_daily; do
  ORIG=$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM $TABLE")
  RESTORED=$(psql "$RESTORE_URL" -tAc "SELECT count(*) FROM $TABLE")
  if [ "$ORIG" != "$RESTORED" ]; then
    echo "MISMATCH in $TABLE: original=$ORIG restored=$RESTORED"
    exit 1
  fi
  echo "$TABLE: OK ($ORIG rows)"
done

psql "$DATABASE_URL" -c "DROP DATABASE $TEST_DB"
echo "restore test passed"
